"""Operating-point maths on synthetic distributions with known answers."""

from __future__ import annotations

import numpy as np
import pytest

from common.face.calibration import (
    calibrate,
    choose_threshold,
    equal_error_rate,
    margin_from_gaps,
    rule_of_three_bound,
    sweep,
)


def test_sweep_covers_zero_to_one_inclusive_with_the_requested_step():
    points = sweep(np.array([0.9]), np.array([0.1]), step=0.005)
    assert len(points) == 201
    assert points[0].threshold == 0.0 and points[-1].threshold == 1.0
    assert points[0].far == 1.0 and points[0].frr == 0.0  # everything accepted at t=0
    assert points[-1].far == 0.0 and points[-1].frr == 1.0  # nothing accepted at t=1


def test_separable_distributions_give_zero_far_and_frr_in_the_gap():
    genuine = np.array([0.80, 0.85, 0.90, 0.95])
    impostor = np.array([0.10, 0.20, 0.30, 0.40])
    chosen = choose_threshold(sweep(genuine, impostor), target_far=0.0)
    # Lowest threshold with FAR == 0 is just above the largest impostor score.
    assert chosen.threshold == pytest.approx(0.405)
    assert chosen.far == 0.0 and chosen.frr == 0.0
    eer, _ = equal_error_rate(sweep(genuine, impostor))
    assert eer == 0.0


def test_overlapping_distributions_trade_far_for_frr():
    rng = np.random.default_rng(0)
    genuine = rng.normal(0.70, 0.08, 2000)
    impostor = rng.normal(0.30, 0.08, 20000)
    point = calibrate(genuine, impostor, gaps=genuine - 0.3, target_far=0.001)
    # Analytic check: FAR 0.1% for N(0.30, 0.08) is at 0.30 + 3.09*0.08 = 0.547.
    assert 0.53 <= point.threshold <= 0.57
    assert point.far <= 0.001
    assert point.frr == pytest.approx(0.03, abs=0.02)  # P(N(0.7,0.08) < 0.55) ~ 3%
    assert 0.45 <= point.eer_threshold <= 0.55 and point.eer < 0.01
    assert point.margin == pytest.approx(np.percentile(genuine - 0.3, 5), abs=1e-6)
    assert point.n_genuine == 2000 and point.n_impostor == 20000
    assert not any("impostor trials" in w for w in point.warnings)


def test_small_sample_warnings_and_rule_of_three():
    genuine = np.array([0.8, 0.9, 0.85])
    impostor = np.array([0.1, 0.2, 0.15, 0.3])
    point = calibrate(genuine, impostor, gaps=np.array([0.5, 0.6, 0.55]), target_far=0.001)
    assert point.far == 0.0
    assert point.far_upper_bound_95 == pytest.approx(3 / 4)
    assert any("Only 4 impostor trials" in w for w in point.warnings)
    assert any("Only 3 genuine trials" in w for w in point.warnings)
    assert rule_of_three_bound(0, 3000) == pytest.approx(0.001)
    assert rule_of_three_bound(0, 0) == 1.0
    assert 0.0 < rule_of_three_bound(5, 1000) < 0.02


def test_margin_is_clipped_at_zero_and_handles_empty():
    assert margin_from_gaps(np.array([])) == 0.0
    assert margin_from_gaps(np.array([-0.2, -0.1, 0.0])) == 0.0
    assert margin_from_gaps(np.array([0.1, 0.2, 0.3, 0.4]), percentile=50) == pytest.approx(0.25)


def test_calibrate_refuses_missing_data():
    with pytest.raises(ValueError, match="genuine"):
        calibrate(np.array([]), np.array([0.1]), np.array([]))
    with pytest.raises(ValueError, match="impostor"):
        calibrate(np.array([0.9]), np.array([]), np.array([]))


def test_overlap_warning_when_an_impostor_beats_a_genuine():
    point = calibrate(
        np.array([0.5, 0.9, 0.9]),
        np.array([0.6, 0.1, 0.1, 0.1]),
        np.array([0.1, 0.4, 0.4]),
        target_far=0.0,
    )
    assert any("overlap" in w for w in point.warnings)
    assert point.frr > 0  # the 0.5 genuine falls below the threshold needed for FAR 0
