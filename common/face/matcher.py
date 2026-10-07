"""Template matching, acceptance rule, 2-of-3 voting and per-session cooldown.

The same code runs on the device (live) and in ``tools/calibrate.py`` (offline), so
the calibrated threshold and margin mean exactly the same thing in both places.

Acceptance for one frame: ``best >= threshold`` and ``best - second_best >= margin``,
where scores are per-student maxima over that student's templates. A student is only
*marked* when the same USN wins ``vote_required`` of the last ``vote_window`` gated
frames, and only once per session (cooldown).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from common.face.types import Array, FloatArray


@dataclass(frozen=True)
class MatchConfig:
    threshold: float
    margin: float = 0.05
    vote_window: int = 3
    vote_required: int = 2

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be within [0, 1]")
        if self.margin < 0:
            raise ValueError("margin must be >= 0")
        if not 1 <= self.vote_required <= self.vote_window:
            raise ValueError("vote_required must be between 1 and vote_window")


@dataclass(frozen=True)
class Template:
    usn: str
    name: str
    embedding: FloatArray


@dataclass(frozen=True)
class MatchResult:
    """Best and runner-up *students* (not templates) for one probe embedding."""

    usn: str
    name: str
    score: float
    second_usn: str | None
    second_score: float

    @property
    def margin(self) -> float:
        return self.score - self.second_score


class TemplateIndex:
    """All templates of a section as one matrix, with per-student max reduction.

    ``scores = T @ e`` is a single BLAS call even for 2,000 students x 3 templates
    (6,000 x 512 float32 = 12 MB), which is why the device can match in microseconds.
    """

    def __init__(self, templates: Sequence[Template]) -> None:
        self._usns: list[str] = []
        self._names: dict[str, str] = {}
        rows: list[Array] = []
        owner: list[int] = []
        index_of: dict[str, int] = {}
        for template in templates:
            vector = np.asarray(template.embedding, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                continue
            if template.usn not in index_of:
                index_of[template.usn] = len(self._usns)
                self._usns.append(template.usn)
                self._names[template.usn] = template.name
            rows.append(vector / norm)
            owner.append(index_of[template.usn])
        self._matrix: FloatArray = (
            np.stack(rows).astype(np.float32) if rows else np.zeros((0, 512), np.float32)
        )
        self._owner = np.asarray(owner, dtype=np.int64)

    @property
    def n_students(self) -> int:
        return len(self._usns)

    @property
    def n_templates(self) -> int:
        return int(self._matrix.shape[0])

    @property
    def usns(self) -> list[str]:
        return list(self._usns)

    def name_of(self, usn: str) -> str:
        return self._names[usn]

    def student_scores(self, embedding: Array) -> FloatArray:
        """Cosine similarity to each student = max over that student's templates."""
        if self.n_templates == 0:
            return np.zeros((0,), np.float32)
        probe = np.asarray(embedding, dtype=np.float32).reshape(-1)
        probe = probe / (np.linalg.norm(probe) or 1.0)
        per_template = self._matrix @ probe
        per_student = np.full((self.n_students,), -1.0, dtype=np.float32)
        np.maximum.at(per_student, self._owner, per_template)
        return per_student

    def match(self, embedding: Array) -> MatchResult | None:
        scores = self.student_scores(embedding)
        if scores.size == 0:
            return None
        order = np.argsort(-scores)
        best = int(order[0])
        second = int(order[1]) if scores.size > 1 else None
        return MatchResult(
            usn=self._usns[best],
            name=self._names[self._usns[best]],
            score=float(scores[best]),
            second_usn=self._usns[second] if second is not None else None,
            second_score=float(scores[second]) if second is not None else -1.0,
        )


def accept(result: MatchResult | None, config: MatchConfig) -> bool:
    """Single-frame acceptance rule shared by the device and the calibration tool."""
    if result is None:
        return False
    return result.score >= config.threshold and result.margin >= config.margin


class FrameVoter:
    """Sliding window over gated frames; a USN wins with ``required`` of the last ``window``."""

    def __init__(self, window: int, required: int) -> None:
        self._votes: deque[str | None] = deque(maxlen=window)
        self._required = required

    def push(self, usn: str | None) -> None:
        self._votes.append(usn)

    def winner(self) -> str | None:
        counts: dict[str, int] = {}
        for vote in self._votes:
            if vote is not None:
                counts[vote] = counts.get(vote, 0) + 1
        for usn, count in counts.items():
            if count >= self._required:
                return usn
        return None

    def exhausted(self) -> bool:
        """True when the window is full and nothing was accepted: show "Not recognised"."""
        return len(self._votes) == self._votes.maxlen and all(v is None for v in self._votes)

    def reset(self) -> None:
        self._votes.clear()


OutcomeKind = Literal["pending", "matched", "already_marked", "not_recognised"]


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    usn: str | None = None
    name: str | None = None
    score: float | None = None
    best_guess: MatchResult | None = None


@dataclass
class Recognizer:
    """Stateful per-session recogniser: match -> accept -> vote -> cooldown."""

    index: TemplateIndex
    config: MatchConfig
    marked: set[str] = field(default_factory=set)
    _voter: FrameVoter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._voter = FrameVoter(self.config.vote_window, self.config.vote_required)

    def observe(self, embedding: Array) -> Outcome:
        """Feed one gated-frame embedding; returns what the UI should show."""
        result = self.index.match(embedding)
        candidate = result.usn if result is not None and accept(result, self.config) else None
        self._voter.push(candidate)
        winner = self._voter.winner()
        if winner is None:
            if self._voter.exhausted():
                self._voter.reset()
                return Outcome("not_recognised", best_guess=result)
            return Outcome("pending", best_guess=result)
        score = result.score if result is not None and result.usn == winner else None
        name = self.index.name_of(winner)
        self._voter.reset()
        if winner in self.marked:
            return Outcome("already_marked", usn=winner, name=name, score=score, best_guess=result)
        self.marked.add(winner)
        return Outcome("matched", usn=winner, name=name, score=score, best_guess=result)

    def mark_known(self, usns: Iterable[str]) -> None:
        """Pre-populate the cooldown (e.g. after a restart mid-session)."""
        self.marked.update(usns)

    def reset_votes(self) -> None:
        self._voter.reset()
