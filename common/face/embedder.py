"""ArcFace MobileFaceNet embedder (``w600k_mbf.onnx``) via onnxruntime.

Input is a 112x112 BGR crop aligned by :mod:`common.face.align`; output is an
L2-normalised 512-d float32 vector, so cosine similarity is a plain dot product. The
byte helpers define the single on-disk/on-wire layout (float32 little-endian, 2048
bytes) used by the server's ``face_templates.embedding`` and the device's SQLite cache.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from common.face.types import Array, FloatArray

EMBEDDER_FILE = "w600k_mbf.onnx"
EMBEDDING_DIM = 512
EMBEDDING_BYTES = EMBEDDING_DIM * 4
INPUT_SIZE = 112


@dataclass(frozen=True)
class EmbedderConfig:
    threads: int = 4


def l2_normalize(vector: Array) -> FloatArray:
    norm = float(np.linalg.norm(vector))
    out = vector / norm if norm > 0 else vector
    return np.asarray(out, dtype=np.float32)


def embedding_to_bytes(vector: Array) -> bytes:
    arr = np.asarray(vector, dtype="<f4").reshape(-1)
    if arr.size != EMBEDDING_DIM:
        raise ValueError(f"embedding must have {EMBEDDING_DIM} values, got {arr.size}")
    return arr.tobytes()


def bytes_to_embedding(data: bytes) -> FloatArray:
    if len(data) != EMBEDDING_BYTES:
        raise ValueError(f"embedding blob must be {EMBEDDING_BYTES} bytes, got {len(data)}")
    return np.frombuffer(data, dtype="<f4").astype(np.float32)


class FaceEmbedder:
    """Computes identity embeddings for aligned crops."""

    def __init__(
        self,
        model_path: Path,
        config: EmbedderConfig | None = None,
        providers: list[str] | None = None,
    ) -> None:
        self.config = config or EmbedderConfig()
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.config.threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(model_path), options, providers=providers or ["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

    def embed(self, aligned_bgr: Array) -> FloatArray:
        return np.asarray(self.embed_batch([aligned_bgr])[0], dtype=np.float32)

    def embed_batch(self, crops: Sequence[Array]) -> FloatArray:
        """``(N, 512)`` normalised embeddings for N aligned 112x112 BGR crops."""
        for crop in crops:
            if crop.shape[:2] != (INPUT_SIZE, INPUT_SIZE):
                raise ValueError(
                    f"aligned crop must be {INPUT_SIZE}x{INPUT_SIZE}, got {crop.shape[:2]}"
                )
        blob = cv2.dnn.blobFromImages(
            list(crops), 1.0 / 127.5, (INPUT_SIZE, INPUT_SIZE), (127.5, 127.5, 127.5), swapRB=True
        )
        raw = self._session.run([self._output_name], {self._input_name: blob})[0]
        raw = np.asarray(raw, dtype=np.float32).reshape(len(crops), -1)
        if raw.shape[1] != EMBEDDING_DIM:
            raise ValueError(f"model returned {raw.shape[1]}-d vectors, expected {EMBEDDING_DIM}")
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(raw / norms, dtype=np.float32)
