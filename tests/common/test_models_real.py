"""End-to-end checks with the real ONNX models on public-domain portraits.

Skipped when ``models/`` is empty (run ``python tools/fetch_models.py``). These guard
the port against regressions that synthetic tests cannot see: a face is found where
there is one, landmarks land inside the box, embeddings are unit length, the same
person under a flip or a rescale stays similar, and two different people do not.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from common.face.align import CROP_SIZE, norm_crop
from common.face.detector import DETECTOR_FILE, DetectorConfig
from common.face.embedder import EMBEDDER_FILE, bytes_to_embedding, embedding_to_bytes
from common.face.engine import EngineConfig, FaceEngine, ModelsMissingError
from common.face.matcher import MatchConfig, Recognizer, Template, TemplateIndex
from common.face.quality import yaw_estimate_deg

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "models"
FIXTURES = REPO / "tests" / "fixtures" / "faces"

pytestmark = pytest.mark.skipif(
    not (MODELS / DETECTOR_FILE).exists() or not (MODELS / EMBEDDER_FILE).exists(),
    reason="ONNX models not downloaded (python tools/fetch_models.py)",
)


@pytest.fixture(scope="module")
def engine() -> FaceEngine:
    return FaceEngine(MODELS, EngineConfig(detector=DetectorConfig(input_size=320)))


@pytest.fixture(scope="module")
def portrait() -> np.ndarray:
    image = cv2.imread(str(FIXTURES / "portrait_2.jpg"))
    assert image is not None
    return image


@pytest.fixture(scope="module")
def other_people() -> np.ndarray:
    image = cv2.imread(str(FIXTURES / "portrait_1.jpg"))
    assert image is not None
    return image


def test_missing_models_give_an_actionable_error(tmp_path):
    with pytest.raises(ModelsMissingError, match="fetch_models"):
        FaceEngine(tmp_path)


def test_detects_one_frontal_face_with_landmarks_inside_the_box(engine, portrait):
    faces = engine.detector.detect(portrait)
    assert len(faces) == 1
    face = faces[0]
    assert face.score > 0.6
    assert face.width > 90
    x1, y1, x2, y2 = face.bbox
    assert all(x1 - 5 <= x <= x2 + 5 and y1 - 5 <= y <= y2 + 5 for x, y in face.kps)
    assert abs(yaw_estimate_deg(face.kps)) < 20


def test_process_runs_the_full_pipeline_and_times_stages(engine, portrait):
    result = engine.process(portrait)
    assert result.ok and result.gate.ok
    assert result.aligned is not None and result.aligned.shape == (CROP_SIZE, CROP_SIZE, 3)
    assert result.embedding is not None and result.embedding.shape == (512,)
    assert np.linalg.norm(result.embedding) == pytest.approx(1.0, abs=1e-4)
    assert {"detect", "align", "embed", "total"} <= set(result.timings_ms)
    assert result.blur is not None and result.blur > 0


def test_same_person_stays_similar_under_flip_and_rescale(engine, portrait):
    base = engine.embed_single(portrait).embedding
    flipped = engine.embed_single(cv2.flip(portrait, 1)).embedding
    smaller = engine.embed_single(cv2.resize(portrait, None, fx=0.8, fy=0.8)).embedding
    assert base is not None and flipped is not None and smaller is not None
    assert float(base @ flipped) > 0.6
    assert float(base @ smaller) > 0.8
    # Shrinking further makes the face narrower than min_face_width_px: "Move closer".
    tiny = engine.embed_single(cv2.resize(portrait, None, fx=0.5, fy=0.5))
    assert tiny.embedding is None and tiny.gate.message == "Move closer"


def test_different_people_score_low(engine, portrait, other_people):
    base = engine.embed_single(portrait).embedding
    assert base is not None
    others = engine.detector.detect(other_people)
    assert len(others) >= 1
    for face in others:
        other = engine.embedder.embed(norm_crop(other_people, face.kps))
        assert float(base @ other) < 0.35


def test_recognizer_marks_the_enrolled_person_and_rejects_strangers(engine, portrait, other_people):
    enrolled = engine.embed_single(portrait).embedding
    assert enrolled is not None
    index = TemplateIndex([Template(usn="1BM22EC001", name="Enrolled", embedding=enrolled)])
    recognizer = Recognizer(index, MatchConfig(threshold=0.4, margin=0.0))

    probe = engine.embed_single(cv2.resize(portrait, None, fx=0.8, fy=0.8)).embedding
    assert probe is not None
    recognizer.observe(probe)
    assert recognizer.observe(probe).kind == "matched"

    stranger = engine.embedder.embed(
        norm_crop(other_people, engine.detector.detect(other_people)[0].kps)
    )
    recognizer2 = Recognizer(index, MatchConfig(threshold=0.4, margin=0.0))
    kinds = [recognizer2.observe(stranger).kind for _ in range(3)]
    assert kinds[-1] == "not_recognised" and "matched" not in kinds


def test_embedding_bytes_round_trip(engine, portrait):
    vector = engine.embed_single(portrait).embedding
    assert vector is not None
    data = embedding_to_bytes(vector)
    assert len(data) == 2048
    assert np.array_equal(bytes_to_embedding(data), vector)
