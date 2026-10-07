"""One object that runs detect -> gates -> align -> blur -> liveness -> embed on a frame.

Used by the device pipeline (live frames), the enrolment tools (ID-card photos and
device captures) and the calibration tool, so every embedding in the system is
produced by exactly the same steps and settings. Stage timings are returned with each
result; ``tools/bench_pi.py`` aggregates them into p50/p95.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from common.face.align import norm_crop
from common.face.detector import DETECTOR_FILE, DetectorConfig, FaceDetector
from common.face.embedder import EMBEDDER_FILE, EmbedderConfig, FaceEmbedder
from common.face.liveness import LivenessCheck, NoOpLiveness
from common.face.quality import GateResult, QualityConfig, check_sharpness, gate, select_face
from common.face.types import Array, Detection, FloatArray


@dataclass(frozen=True)
class EngineConfig:
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    quality: QualityConfig = field(default_factory=QualityConfig)
    embedder: EmbedderConfig = field(default_factory=EmbedderConfig)


@dataclass
class FrameResult:
    """Everything the pipeline learned about one frame."""

    detections: list[Detection]
    gate: GateResult
    face: Detection | None = None
    aligned: Array | None = None
    blur: float | None = None
    embedding: FloatArray | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.embedding is not None


class ModelsMissingError(FileNotFoundError):
    """The ONNX files are not where config says. Fix: ``python tools/fetch_models.py``."""


class FaceEngine:
    def __init__(
        self,
        models_dir: Path,
        config: EngineConfig | None = None,
        liveness: LivenessCheck | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self.liveness: LivenessCheck = liveness or NoOpLiveness()
        detector_path = models_dir / DETECTOR_FILE
        embedder_path = models_dir / EMBEDDER_FILE
        missing = [p.name for p in (detector_path, embedder_path) if not p.is_file()]
        if missing:
            raise ModelsMissingError(
                f"missing {', '.join(missing)} in {models_dir}; run: python tools/fetch_models.py"
            )
        self.detector = FaceDetector(detector_path, self.config.detector)
        self.embedder = FaceEmbedder(embedder_path, self.config.embedder)

    def process(self, frame_bgr: Array, *, embed: bool = True) -> FrameResult:
        """Run the whole pipeline on one BGR frame. Never raises on bad frames."""
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        detections = self.detector.detect(frame_bgr)
        timings["detect"] = (time.perf_counter() - t0) * 1000

        verdict, face = select_face(detections, self.config.quality)
        result = FrameResult(detections=detections, gate=verdict, face=face, timings_ms=timings)
        if not verdict.ok or face is None:
            return result

        t1 = time.perf_counter()
        aligned = norm_crop(frame_bgr, face.kps)
        timings["align"] = (time.perf_counter() - t1) * 1000
        result.aligned = aligned

        sharp, blur = check_sharpness(aligned, self.config.quality)
        result.blur = blur
        if not sharp.ok:
            result.gate = sharp
            return result

        live = self.liveness.check(frame_bgr, face, aligned)
        if not live.passed:
            result.gate = gate("liveness")
            return result

        if embed:
            t2 = time.perf_counter()
            result.embedding = self.embedder.embed(aligned)
            timings["embed"] = (time.perf_counter() - t2) * 1000
        timings["total"] = (time.perf_counter() - t0) * 1000
        return result

    def embed_single(self, image_bgr: Array) -> FrameResult:
        """For enrolment photos: same gates as live, but blur is reported, not enforced.

        ID-card scans are often soft; the caller decides with ``result.blur`` and the
        configured threshold whether to accept, and records the value as quality_score.
        """
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        detections = self.detector.detect(image_bgr)
        timings["detect"] = (time.perf_counter() - t0) * 1000
        verdict, face = select_face(detections, self.config.quality)
        result = FrameResult(detections=detections, gate=verdict, face=face, timings_ms=timings)
        if not verdict.ok or face is None:
            return result
        aligned = norm_crop(image_bgr, face.kps)
        result.aligned = aligned
        _, result.blur = check_sharpness(aligned, self.config.quality)
        result.embedding = self.embedder.embed(aligned)
        timings["total"] = (time.perf_counter() - t0) * 1000
        return result
