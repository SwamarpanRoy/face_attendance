"""Operating-point selection from measured genuine and impostor scores.

Pure numpy, no I/O, so it can be tested on synthetic distributions with known answers.
``tools/calibrate.py`` feeds it scores produced by the same ``TemplateIndex`` the device
uses, which is what keeps calibration and live behaviour from drifting apart.

Definitions (scores are cosine similarities, higher = more similar):

* FAR(t): fraction of impostor scores >= t (a stranger accepted).
* FRR(t): fraction of genuine scores  < t (the right person rejected).
* threshold: the *lowest* t whose FAR is <= the target, i.e. the most permissive
  setting that still meets the false-accept budget (lowest FRR).
* EER: where FAR and FRR cross.
* margin: a low percentile of (own score - best other score) over genuine probes, so a
  typical genuine match clears the runner-up by at least this much.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from common.face.types import Array


@dataclass(frozen=True)
class SweepPoint:
    threshold: float
    far: float
    frr: float


@dataclass(frozen=True)
class OperatingPoint:
    threshold: float
    far: float
    frr: float
    target_far: float
    eer: float
    eer_threshold: float
    margin: float
    n_genuine: int
    n_impostor: int
    far_upper_bound_95: float
    warnings: tuple[str, ...]


def sweep(genuine: Array, impostor: Array, step: float = 0.005) -> list[SweepPoint]:
    """FAR/FRR at every threshold from 0 to 1 inclusive, in ``step`` increments."""
    genuine = np.asarray(genuine, dtype=np.float64)
    impostor = np.asarray(impostor, dtype=np.float64)
    thresholds = np.round(np.arange(0.0, 1.0 + step / 2, step), 6)
    points: list[SweepPoint] = []
    for t in thresholds:
        far = float(np.mean(impostor >= t)) if impostor.size else 0.0
        frr = float(np.mean(genuine < t)) if genuine.size else 0.0
        points.append(SweepPoint(float(t), far, frr))
    return points


def equal_error_rate(points: list[SweepPoint]) -> tuple[float, float]:
    """(EER, threshold) at the sweep point where |FAR - FRR| is smallest."""
    best = min(points, key=lambda p: (abs(p.far - p.frr), p.threshold))
    return (best.far + best.frr) / 2.0, best.threshold


def choose_threshold(points: list[SweepPoint], target_far: float) -> SweepPoint:
    """Lowest threshold meeting the FAR target; the last point if none does."""
    for point in points:
        if point.far <= target_far:
            return point
    return points[-1]


def margin_from_gaps(gaps: Array, percentile: float = 5.0) -> float:
    """Non-negative margin at the given percentile of genuine gaps (0 when no gaps)."""
    gaps = np.asarray(gaps, dtype=np.float64)
    if gaps.size == 0:
        return 0.0
    return max(0.0, float(np.percentile(gaps, percentile)))


def rule_of_three_bound(events: int, trials: int) -> float:
    """95% upper confidence bound on a rate. With zero events in N trials it is 3/N."""
    if trials <= 0:
        return 1.0
    if events == 0:
        return min(1.0, 3.0 / trials)
    # Normal approximation for events > 0 (adequate for reporting purposes).
    rate = events / trials
    return min(1.0, rate + 1.96 * float(np.sqrt(rate * (1 - rate) / trials)))


def calibrate(
    genuine: Array,
    impostor: Array,
    gaps: Array,
    *,
    target_far: float = 0.001,
    margin_percentile: float = 5.0,
    step: float = 0.005,
) -> OperatingPoint:
    """Pick threshold and margin and spell out the statistical limits of the data."""
    genuine = np.asarray(genuine, dtype=np.float64)
    impostor = np.asarray(impostor, dtype=np.float64)
    if genuine.size == 0:
        raise ValueError("no genuine scores: capture probes for enrolled students first")
    if impostor.size == 0:
        raise ValueError("no impostor scores: at least two enrolled students are needed")

    points = sweep(genuine, impostor, step)
    chosen = choose_threshold(points, target_far)
    eer, eer_threshold = equal_error_rate(points)
    false_accepts = int(np.sum(impostor >= chosen.threshold))
    bound = rule_of_three_bound(false_accepts, int(impostor.size))

    warnings: list[str] = []
    if target_far > 0 and impostor.size < 1.0 / target_far:
        warnings.append(
            f"Only {impostor.size} impostor trials: a FAR of {target_far:g} cannot be "
            f"measured directly (needs >= {round(1 / target_far)}). With "
            f"{false_accepts} false accepts the 95% upper bound on FAR is {bound:.3g}."
        )
    if genuine.size < 30:
        warnings.append(
            f"Only {genuine.size} genuine trials: FRR has a resolution of {1 / genuine.size:.1%}."
        )
    if chosen.frr > 0.10:
        warnings.append(
            f"FRR at the chosen threshold is {chosen.frr:.1%}: expect frequent "
            "'Not recognised'. More or better probes/templates will help."
        )
    if float(np.max(impostor)) >= float(np.min(genuine)):
        warnings.append(
            "Genuine and impostor scores overlap: some probes may be mislabelled or some "
            "students look alike to the model; inspect the report before trusting it."
        )

    return OperatingPoint(
        threshold=chosen.threshold,
        far=chosen.far,
        frr=chosen.frr,
        target_far=target_far,
        eer=eer,
        eer_threshold=eer_threshold,
        margin=margin_from_gaps(gaps, margin_percentile),
        n_genuine=int(genuine.size),
        n_impostor=int(impostor.size),
        far_upper_bound_95=bound,
        warnings=tuple(warnings),
    )
