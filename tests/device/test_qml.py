"""The QML screens load and bind to the controller without a single QML error.

Rendering is not needed (the window stays hidden), but every component, binding and
JavaScript expression is created and evaluated, so a typo, a missing property on the
controller or a broken import fails here instead of on the Pi.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtQml import QQmlApplicationEngine

from device.app.ui.app import load_ui
from tests.device.helpers import TEACHER, enter_pin, wait
from tests.device.test_enrol_flow import FakeClient, FakeSyncer


def test_every_screen_loads_without_qml_errors(world):
    device, store, _ = world
    engine = QQmlApplicationEngine()
    problems: list[str] = []
    engine.warnings.connect(lambda warnings: problems.extend(w.toString() for w in warnings))
    load_ui(engine, device, visible=False)
    root = engine.rootObjects()[0]
    assert root.property("width") == 480 and root.property("height") == 800

    def settle() -> None:
        wait(lambda: False, 150)

    settle()  # idle
    device.startSession()
    settle()
    device.startNext()
    device.startNext()
    device.startNext()
    settle()  # pin
    enter_pin(device, "1234")
    wait(lambda: device.bannerTone in ("ok", "warn"))
    settle()  # live with a result card
    device.endSession()
    enter_pin(device, "1234")
    device.openSettings()
    enter_pin(device, "9999")
    settle()  # settings
    store.set_setting("consent_version", "v1")
    store.set_setting("consent_notice", "notice")
    from device.app.enrol import EnrolmentFlow

    device.start_enrol(EnrolmentFlow(FakeClient(), store, FakeSyncer()), TEACHER)  # type: ignore[arg-type]
    settle()  # enrol: pick
    device.enrolChoose("1BM22EC001")
    settle()  # consent
    device.enrolConsent()
    wait(lambda: device.enrolPage == "done", timeout_ms=8000)
    settle()  # done
    device.enrolClose()
    settle()

    assert problems == []
