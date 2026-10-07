"""SCRFD decoding and NMS on synthetic network outputs (no model file needed)."""

from __future__ import annotations

import numpy as np
import pytest

from common.face.detector import (
    NUM_ANCHORS,
    STRIDES,
    anchor_centers,
    decode_outputs,
    distance2bbox,
    distance2kps,
    letterbox,
    nms,
)


def _zero_outputs(input_size: int, batched: bool = False) -> list[np.ndarray]:
    outs: list[np.ndarray] = []
    for kind_width in (1, 4, 10):  # scores, bboxes, kps
        for stride in STRIDES:
            n = (input_size // stride) ** 2 * NUM_ANCHORS
            arr = np.zeros((n, kind_width), np.float32)
            outs.append(arr[None] if batched else arr)
    return outs


def test_anchor_centers_follow_the_scrfd_layout():
    centers = anchor_centers(input_size=32, stride=16)
    expected = [(0, 0), (0, 0), (16, 0), (16, 0), (0, 16), (0, 16), (16, 16), (16, 16)]
    assert centers.tolist() == [list(map(float, p)) for p in expected]


def test_distance_decoders():
    points = np.array([[10.0, 20.0]])
    assert distance2bbox(points, np.array([[1.0, 2.0, 3.0, 4.0]])).tolist() == [
        [9.0, 18.0, 13.0, 24.0]
    ]
    kps = distance2kps(points, np.array([[1.0, 1.0, -1.0, 2.0, 0.0, 0.0, 5.0, 5.0, -5.0, -5.0]]))
    assert kps.tolist() == [[11.0, 21.0, 9.0, 22.0, 10.0, 20.0, 15.0, 25.0, 5.0, 15.0]]


@pytest.mark.parametrize("batched", [False, True])
def test_decode_places_a_box_at_the_right_anchor(batched):
    size = 64
    outs = _zero_outputs(size, batched)
    stride_idx = STRIDES.index(16)
    # Second grid cell (x=16, y=0) at stride 16, first of its two anchors -> row 2.
    row = 1 * NUM_ANCHORS
    scores, bboxes, kps = outs[stride_idx], outs[stride_idx + 3], outs[stride_idx + 6]
    view = (lambda a: a[0]) if batched else (lambda a: a)
    view(scores)[row, 0] = 0.9
    view(bboxes)[row] = [1.0, 2.0, 3.0, 4.0]  # in stride units
    view(kps)[row] = [0.5, 0.5] * 5

    boxes, confidences, landmarks = decode_outputs(outs, size, score_threshold=0.5)

    assert confidences.tolist() == [pytest.approx(0.9)]
    assert boxes.tolist() == [[16 - 16, 0 - 32, 16 + 48, 0 + 64]]
    assert landmarks.shape == (1, 5, 2)
    assert landmarks[0].tolist() == [[24.0, 8.0]] * 5


def test_decode_filters_by_threshold_and_returns_empty_arrays():
    boxes, scores, kps = decode_outputs(_zero_outputs(64), 64, score_threshold=0.5)
    assert boxes.shape == (0, 4) and scores.shape == (0,) and kps.shape == (0, 5, 2)


def test_decode_rejects_mismatched_input_size():
    with pytest.raises(ValueError, match="does not match"):
        decode_outputs(_zero_outputs(64), 96, score_threshold=0.5)


def test_nms_suppresses_overlaps_and_sorts_by_score():
    boxes = np.array(
        [[0, 0, 100, 100], [5, 5, 105, 105], [200, 200, 300, 300], [0, 0, 100, 100]],
        dtype=np.float32,
    )
    scores = np.array([0.8, 0.9, 0.7, 0.1], dtype=np.float32)
    keep = nms(boxes, scores, iou_threshold=0.4)
    assert keep.tolist() == [1, 2]
    assert nms(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), 0.4).size == 0


def test_letterbox_keeps_aspect_ratio_and_pads_bottom_right():
    tall = np.full((200, 100, 3), 255, np.uint8)
    canvas, scale = letterbox(tall, 320)
    assert canvas.shape == (320, 320, 3)
    assert scale == pytest.approx(1.6)
    assert canvas[:, :160].min() == 255 and canvas[:, 160:].max() == 0

    wide = np.full((100, 400, 3), 255, np.uint8)
    canvas, scale = letterbox(wide, 320)
    assert scale == pytest.approx(0.8)
    assert canvas[:80].min() == 255 and canvas[80:].max() == 0
