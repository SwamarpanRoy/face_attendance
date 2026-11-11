"""Device test fixtures: a Qt app and a controller over a real store and pipeline."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtGui import QGuiApplication

from common.face.matcher import MatchConfig, Recognizer, TemplateIndex
from device.app.camera import ImageSource
from device.app.config import DeviceConfig
from device.app.controller import DeviceController
from device.app.pipeline import PipelineWorker
from device.app.session import SessionManager
from device.app.store import DeviceStore
from tests.device.helpers import FIXTURE, MODEL, FakeEngine, FakeVerifier, unit


@pytest.fixture(scope="module")
def qapp():
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def world(tmp_path, qapp):
    store = DeviceStore(tmp_path / "device.db")
    store.put_catalog("courses", [{"id": 1, "code": "22EC71", "name": "VLSI"}])
    store.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}])
    store.put_catalog(
        "periods",
        [{"id": 1, "ordinal": 1, "name": "P1", "start_time": "09:00", "end_time": "10:00"}],
    )
    store.put_catalog("offerings", [{"id": 1, "course_id": 1, "section_id": 1, "faculty_id": 7}])
    store.put_catalog(
        "roster:1", [{"id": i, "usn": f"1BM22EC00{i}", "name": f"S{i}"} for i in range(1, 4)]
    )
    identity = unit(1)
    store.add_template("1BM22EC001", "Aditi", identity, MODEL, section_id=1)
    config = DeviceConfig()
    worker = PipelineWorker(
        ImageSource(FIXTURE, fps=120),
        FakeEngine(identity),
        Recognizer(TemplateIndex([]), MatchConfig(0.4)),
    )  # type: ignore[arg-type]
    device = DeviceController(
        config=config,
        store=store,
        worker=worker,
        sessions=SessionManager(store, config),
        client=None,
        verifier=FakeVerifier(),  # type: ignore[arg-type]
        network_info=lambda: {"ssid": "test", "ip": "10.0.0.2"},
        clock_synced=lambda: True,
    )
    device.start_workers()
    yield device, store, worker
    device.shutdown()
    store.close()
