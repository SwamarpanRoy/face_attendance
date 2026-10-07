"""SCRFD face detector (``det_500m.onnx`` from InsightFace ``buffalo_s``) via onnxruntime.

Only the pieces the device needs are ported from ``insightface.model_zoo.scrfd``:
letterbox preprocessing, anchor-centre decoding of the three FPN strides, 5-landmark
decoding and greedy NMS. ``decode_outputs`` and ``nms`` are pure functions so they can
be unit-tested on synthetic network outputs without a model file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from common.face.types import Array, Detection

DETECTOR_FILE = "det_500m.onnx"
STRIDES: tuple[int, ...] = (8, 16, 32)
NUM_ANCHORS = 2
NUM_LANDMARKS = 5


@dataclass(frozen=True)
class DetectorConfig:
    """Tunables. ``input_size`` trades latency for small-face recall (320 or 480 on the Pi)."""

    input_size: int = 320
    score_threshold: float = 0.5
    nms_iou: float = 0.4
    max_faces: int = 10
    threads: int = 4


def distance2bbox(points: Array, distance: Array) -> Array:
    """Anchor centres + (left, top, right, bottom) distances -> ``[x1, y1, x2, y2]``."""
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def distance2kps(points: Array, distance: Array) -> Array:
    """Anchor centres + per-landmark (dx, dy) offsets -> ``(N, 2*K)`` landmark coordinates."""
    preds = []
    for i in range(0, distance.shape[1], 2):
        preds.append(points[:, i % 2] + distance[:, i])
        preds.append(points[:, i % 2 + 1] + distance[:, i + 1])
    return np.stack(preds, axis=-1)


def anchor_centers(input_size: int, stride: int, num_anchors: int = NUM_ANCHORS) -> Array:
    """``(h*w*num_anchors, 2)`` grid of (x, y) anchor centres in input pixels."""
    height = width = input_size // stride
    ys, xs = np.mgrid[:height, :width]
    grid = np.stack([xs, ys], axis=-1).astype(np.float32) * stride
    centers = grid.reshape((-1, 2))
    if num_anchors > 1:
        centers = np.stack([centers] * num_anchors, axis=1).reshape((-1, 2))
    return np.asarray(centers, dtype=np.float32)


def decode_outputs(
    outputs: list[Array],
    input_size: int,
    score_threshold: float,
    strides: tuple[int, ...] = STRIDES,
    num_anchors: int = NUM_ANCHORS,
) -> tuple[Array, Array, Array]:
    """Turn raw network outputs into candidate boxes, scores and landmarks (input pixels).

    Output layout for SCRFD with landmarks: ``[scores x3, bboxes x3, kps x3]`` ordered
    by stride. Returns ``boxes (N,4)``, ``scores (N,)``, ``kps (N,5,2)`` above threshold,
    not yet suppressed or sorted.
    """
    fmc = len(strides)
    boxes_all: list[Array] = []
    scores_all: list[Array] = []
    kps_all: list[Array] = []
    for idx, stride in enumerate(strides):
        scores = np.asarray(outputs[idx])
        bbox_preds = np.asarray(outputs[idx + fmc])
        kps_preds = np.asarray(outputs[idx + 2 * fmc])
        if scores.ndim == 3:  # batched export: drop the batch axis
            scores, bbox_preds, kps_preds = scores[0], bbox_preds[0], kps_preds[0]
        scores = scores.reshape(-1)
        bbox_preds = bbox_preds.reshape(-1, 4) * stride
        kps_preds = kps_preds.reshape(-1, 2 * NUM_LANDMARKS) * stride
        centers = anchor_centers(input_size, stride, num_anchors)
        if centers.shape[0] != scores.shape[0]:
            raise ValueError(
                f"stride {stride}: {scores.shape[0]} scores but {centers.shape[0]} anchors; "
                f"input_size {input_size} does not match the model output"
            )
        keep = np.where(scores >= score_threshold)[0]
        if keep.size == 0:
            continue
        boxes_all.append(distance2bbox(centers, bbox_preds)[keep])
        scores_all.append(scores[keep])
        kps_all.append(distance2kps(centers, kps_preds)[keep].reshape(-1, NUM_LANDMARKS, 2))
    if not boxes_all:
        empty = np.zeros((0, 4), np.float32), np.zeros((0,), np.float32)
        return (*empty, np.zeros((0, NUM_LANDMARKS, 2), np.float32))
    return np.concatenate(boxes_all), np.concatenate(scores_all), np.concatenate(kps_all)


def nms(boxes: Array, scores: Array, iou_threshold: float) -> Array:
    """Greedy non-maximum suppression; returns kept indices sorted by descending score."""
    if boxes.shape[0] == 0:
        return np.zeros((0,), dtype=np.int64)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(iou <= iou_threshold)[0] + 1]
    return np.asarray(keep, dtype=np.int64)


def letterbox(frame_bgr: Array, input_size: int) -> tuple[Array, float]:
    """Resize so the longer side fits ``input_size`` and pad bottom/right with black.

    Returns the padded image and the scale applied (multiply detections by ``1/scale``
    to get back to frame coordinates). Mirrors the reference ``SCRFD.detect`` resize.
    """
    height, width = frame_bgr.shape[:2]
    im_ratio = height / width
    if im_ratio > 1.0:
        new_h = input_size
        new_w = int(new_h / im_ratio)
    else:
        new_w = input_size
        new_h = int(new_w * im_ratio)
    scale = new_h / height
    resized = cv2.resize(frame_bgr, (new_w, new_h))
    canvas = np.zeros((input_size, input_size, 3), dtype=np.uint8)
    canvas[:new_h, :new_w, :] = resized
    return canvas, scale


class FaceDetector:
    """Runs det_500m on a frame and returns :class:`Detection` objects in frame coordinates."""

    def __init__(
        self,
        model_path: Path,
        config: DetectorConfig | None = None,
        providers: list[str] | None = None,
    ) -> None:
        self.config = config or DetectorConfig()
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.config.threads
        options.inter_op_num_threads = 1
        # det_500m has 640x640 output shapes baked into its graph; running at 320 or 480
        # is fine but makes onnxruntime warn on every call. Errors only.
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(model_path), options, providers=providers or ["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        if len(self._output_names) != 3 * len(STRIDES):
            raise ValueError(
                f"{model_path.name}: expected {3 * len(STRIDES)} outputs (scores/bboxes/kps "
                f"per stride), got {len(self._output_names)}"
            )

    def detect(self, frame_bgr: Array) -> list[Detection]:
        """Detect faces, strongest first, capped at ``config.max_faces``."""
        size = self.config.input_size
        canvas, scale = letterbox(frame_bgr, size)
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128.0, (size, size), (127.5, 127.5, 127.5), swapRB=True
        )
        outputs = self._session.run(self._output_names, {self._input_name: blob})
        boxes, scores, kps = decode_outputs(outputs, size, self.config.score_threshold)
        keep = nms(boxes, scores, self.config.nms_iou)[: self.config.max_faces]
        return [
            Detection(
                bbox=(boxes[i] / scale).astype(np.float32),
                score=float(scores[i]),
                kps=(kps[i] / scale).astype(np.float32),
            )
            for i in keep
        ]
