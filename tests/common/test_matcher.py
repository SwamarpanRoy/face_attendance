"""Threshold, margin, per-student max, 2-of-3 voting and cooldown."""

from __future__ import annotations

import numpy as np
import pytest

from common.face.matcher import (
    FrameVoter,
    MatchConfig,
    Recognizer,
    Template,
    TemplateIndex,
    accept,
)

DIM = 512


def _unit(rng: np.random.Generator) -> np.ndarray:
    v = rng.normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def people():
    rng = np.random.default_rng(42)
    base = {usn: _unit(rng) for usn in ("1BM22EC001", "1BM22EC002", "1BM22EC003")}
    templates = []
    # Per-dimension noise of 0.01 on a 512-d unit vector is a perturbation of norm ~0.23,
    # i.e. cosine ~0.97 to the base identity: realistic "same person, different frame".
    for usn, vector in base.items():
        for _ in range(3):
            noisy = vector + rng.normal(scale=0.01, size=DIM).astype(np.float32)
            templates.append(Template(usn=usn, name=f"Student {usn[-1]}", embedding=noisy))
    return base, templates


def _probe(vector: np.ndarray, rng: np.random.Generator, noise: float = 0.01) -> np.ndarray:
    return vector + rng.normal(scale=noise, size=DIM).astype(np.float32)


def test_index_groups_templates_per_student_and_takes_the_max(people):
    base, templates = people
    index = TemplateIndex(templates)
    assert index.n_students == 3 and index.n_templates == 9

    probe = base["1BM22EC002"]
    scores = index.student_scores(probe)
    assert scores.shape == (3,)
    assert index.usns[int(scores.argmax())] == "1BM22EC002"
    # Max over templates is at least the score of any single template of that student.
    own = [t.embedding for t in templates if t.usn == "1BM22EC002"]
    singles = [float(np.dot(e / np.linalg.norm(e), probe)) for e in own]
    assert scores.max() == pytest.approx(max(singles), abs=1e-5)


def test_match_result_has_best_and_runner_up(people):
    base, templates = people
    result = TemplateIndex(templates).match(base["1BM22EC003"])
    assert result is not None
    assert result.usn == "1BM22EC003" and result.name == "Student 3"
    assert result.second_usn in ("1BM22EC001", "1BM22EC002")
    assert result.score > 0.9 and result.second_score < 0.3
    assert result.margin == pytest.approx(result.score - result.second_score)


def test_accept_requires_threshold_and_margin(people):
    base, templates = people
    result = TemplateIndex(templates).match(base["1BM22EC001"])
    assert accept(result, MatchConfig(threshold=0.5, margin=0.05))
    assert not accept(result, MatchConfig(threshold=0.99, margin=0.05))
    assert not accept(result, MatchConfig(threshold=0.5, margin=0.99))
    assert not accept(None, MatchConfig(threshold=0.1))


def test_unknown_face_scores_low_against_everyone(people):
    _, templates = people
    rng = np.random.default_rng(7)
    stranger = _unit(rng)
    result = TemplateIndex(templates).match(stranger)
    assert result is not None and result.score < 0.3


def test_empty_index_matches_nothing():
    assert TemplateIndex([]).match(np.ones(DIM, np.float32)) is None
    assert TemplateIndex([]).n_students == 0


def test_match_config_validation():
    with pytest.raises(ValueError):
        MatchConfig(threshold=1.5)
    with pytest.raises(ValueError):
        MatchConfig(threshold=0.4, vote_required=4, vote_window=3)
    with pytest.raises(ValueError):
        MatchConfig(threshold=0.4, margin=-1)


def test_voter_two_of_three():
    voter = FrameVoter(window=3, required=2)
    voter.push("A")
    assert voter.winner() is None
    voter.push(None)
    assert voter.winner() is None
    voter.push("A")
    assert voter.winner() == "A"

    voter.reset()
    for vote in ("A", "B", "C"):
        voter.push(vote)
    assert voter.winner() is None
    voter.push("B")  # window is now B, C, B
    assert voter.winner() == "B"

    voter.reset()
    for _ in range(3):
        voter.push(None)
    assert voter.winner() is None and voter.exhausted()


def test_recognizer_marks_once_then_reports_already_marked(people):
    base, templates = people
    rng = np.random.default_rng(1)
    recognizer = Recognizer(TemplateIndex(templates), MatchConfig(threshold=0.5, margin=0.05))

    first = recognizer.observe(_probe(base["1BM22EC001"], rng))
    assert first.kind == "pending"
    second = recognizer.observe(_probe(base["1BM22EC001"], rng))
    assert second.kind == "matched" and second.usn == "1BM22EC001" and second.name == "Student 1"
    assert second.score is not None and second.score > 0.9

    # Same person again in the same session: no new record.
    recognizer.observe(_probe(base["1BM22EC001"], rng))
    again = recognizer.observe(_probe(base["1BM22EC001"], rng))
    assert again.kind == "already_marked" and again.usn == "1BM22EC001"
    assert recognizer.marked == {"1BM22EC001"}


def test_recognizer_reports_not_recognised_after_a_full_window_of_rejections(people):
    _, templates = people
    rng = np.random.default_rng(3)
    recognizer = Recognizer(TemplateIndex(templates), MatchConfig(threshold=0.5, margin=0.05))
    outcomes = [recognizer.observe(_unit(rng)).kind for _ in range(3)]
    assert outcomes == ["pending", "pending", "not_recognised"]
    assert recognizer.marked == set()


def test_recognizer_does_not_mix_two_people_into_a_vote(people):
    base, templates = people
    rng = np.random.default_rng(5)
    recognizer = Recognizer(TemplateIndex(templates), MatchConfig(threshold=0.5, margin=0.05))
    assert recognizer.observe(_probe(base["1BM22EC001"], rng)).kind == "pending"
    assert recognizer.observe(_probe(base["1BM22EC002"], rng)).kind == "pending"
    assert recognizer.observe(_probe(base["1BM22EC003"], rng)).kind == "pending"
    outcome = recognizer.observe(_probe(base["1BM22EC003"], rng))
    assert outcome.kind == "matched" and outcome.usn == "1BM22EC003"


def test_mark_known_prepopulates_cooldown(people):
    base, templates = people
    rng = np.random.default_rng(9)
    recognizer = Recognizer(TemplateIndex(templates), MatchConfig(threshold=0.5))
    recognizer.mark_known(["1BM22EC002"])
    recognizer.observe(_probe(base["1BM22EC002"], rng))
    assert recognizer.observe(_probe(base["1BM22EC002"], rng)).kind == "already_marked"
