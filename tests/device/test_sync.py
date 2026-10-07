"""Template/catalog sync merges server answers into the local cache (fake client, no network)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import numpy as np
import pytest

from common.face.embedder import embedding_to_bytes
from common.schemas import (
    CatalogOut,
    CourseOut,
    FacultyPinOut,
    OfferingOut,
    PeriodOut,
    SectionOut,
    StudentTemplatesOut,
    TemplateOut,
    TemplatesOut,
)
from device.app.store import DeviceStore
from device.app.sync import ModelMismatchError, Syncer, apply_catalog, apply_templates

MODEL = "buffalo_s/w600k_mbf"


def _vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def _template(idx: int, seed: int) -> TemplateOut:
    return TemplateOut(
        idx=idx,
        embedding_b64=base64.b64encode(embedding_to_bytes(_vec(seed))).decode(),
        model_version=MODEL,
        created_at=datetime.now(UTC),
    )


def _payload(students, *, full, deleted=(), calibrated=False, model=MODEL) -> TemplatesOut:
    return TemplatesOut(
        generated_at=datetime.now(UTC),
        model_version=model,
        calibrated=calibrated,
        threshold=0.47 if calibrated else None,
        margin=0.06 if calibrated else None,
        full=full,
        students=students,
        deleted_usns=list(deleted),
    )


def test_full_sync_replaces_cache_and_stores_calibration(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    store.add_template("STALE", "Old", _vec(99), MODEL)
    payload = _payload(
        [
            StudentTemplatesOut(
                usn="1BM22EC001",
                name="Aditi",
                section_id=1,
                templates=[_template(0, 1), _template(1, 2)],
            ),
            StudentTemplatesOut(
                usn="1BM22EC002", name="Arjun", section_id=1, templates=[_template(0, 3)]
            ),
        ],
        full=True,
        calibrated=True,
    )
    result = apply_templates(store, payload, MODEL)
    assert (result.full, result.students, result.templates, result.deleted, result.calibrated) == (
        True,
        2,
        3,
        0,
        True,
    )
    assert sorted({t.usn for t in store.load_templates()}) == ["1BM22EC001", "1BM22EC002"]
    assert store.calibration() == (pytest.approx(0.47), pytest.approx(0.06))
    assert store.get_setting("templates_since") == payload.generated_at.isoformat()
    assert np.allclose(store.load_templates(section_id=1)[0].embedding, _vec(1))
    store.close()


def test_incremental_sync_applies_deletions_then_upserts(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    apply_templates(
        store,
        _payload(
            [
                StudentTemplatesOut(usn="A", name="A", section_id=1, templates=[_template(0, 1)]),
                StudentTemplatesOut(usn="B", name="B", section_id=1, templates=[_template(0, 2)]),
            ],
            full=True,
            calibrated=True,
        ),
        MODEL,
    )
    delta = _payload(
        [
            StudentTemplatesOut(
                usn="A",
                name="A renamed",
                section_id=1,
                templates=[_template(0, 5), _template(1, 6)],
            )
        ],
        full=False,
        deleted=["B"],
        calibrated=False,
    )
    result = apply_templates(store, delta, MODEL)
    assert (result.full, result.deleted, result.templates) == (False, 1, 2)
    templates = store.load_templates()
    assert [t.usn for t in templates] == ["A", "A"] and templates[0].name == "A renamed"
    assert store.calibration() is None  # server says not calibrated -> fallbacks again
    store.close()


def test_model_mismatch_is_refused_before_touching_the_cache(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    store.add_template("KEEP", "Keep", _vec(1), MODEL)
    with pytest.raises(ModelMismatchError):
        apply_templates(store, _payload([], full=True, model="other/model"), MODEL)
    assert [t.usn for t in store.load_templates()] == ["KEEP"]
    store.close()


def test_catalog_sync_stores_everything_the_device_needs_offline(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    payload = CatalogOut(
        generated_at=datetime.now(UTC),
        courses=[CourseOut(id=1, code="22EC71", name="VLSI")],
        sections=[SectionOut(id=1, name="ECE-7A")],
        periods=[PeriodOut(id=1, ordinal=1, name="P1", start_time="09:00", end_time="10:00")],
        offerings=[OfferingOut(course_id=1, section_id=1, faculty_id=7)],
        faculty=[FacultyPinOut(id=7, name="Prof", role="faculty", pin_hash="$argon2id$x")],
        consent_version="2026-10-v1",
        consent_notice="notice text",
    )
    apply_catalog(store, payload)
    assert store.get_catalog("courses")[0]["code"] == "22EC71"
    assert store.get_catalog("offerings") == [
        {"id": 1, "course_id": 1, "section_id": 1, "faculty_id": 7}
    ]
    assert store.faculty_pins() == [
        {"faculty_id": 7, "name": "Prof", "role": "faculty", "pin_hash": "$argon2id$x"}
    ]
    assert store.get_setting("consent_version") == "2026-10-v1"
    assert store.get_setting("consent_notice") == "notice text"
    store.close()


class FakeClient:
    def __init__(self) -> None:
        self.since_values = []
        self.catalog_calls = 0

    def templates(self, since=None):
        self.since_values.append(since)
        return _payload(
            [StudentTemplatesOut(usn="A", name="A", section_id=1, templates=[_template(0, 1)])],
            full=since is None,
        )

    def catalog(self):
        self.catalog_calls += 1
        return CatalogOut(
            generated_at=datetime.now(UTC),
            courses=[],
            sections=[],
            periods=[],
            offerings=[],
            faculty=[],
        )


def test_syncer_uses_the_stored_since_on_the_second_pull(tmp_path):
    store = DeviceStore(tmp_path / "d.db")
    client = FakeClient()
    syncer = Syncer(store, client, MODEL)  # type: ignore[arg-type]
    first = syncer.pull_all()
    second = syncer.pull_templates()
    forced = syncer.pull_templates(full=True)
    assert first.full is True and second.full is False and forced.full is True
    assert (
        client.since_values[0] is None
        and client.since_values[1] is not None
        and client.since_values[2] is None
    )
    assert client.catalog_calls == 1
    store.close()
