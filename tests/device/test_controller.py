"""Walks the device through whole flows via the controller the QML screens bind to.

No QML here (tests/device/test_qml.py loads the screens): PINs are typed into the PIN
screen state, frames come from a still image through the real pipeline worker with a
fake engine, and everything else runs through the real session manager and store.
"""

from __future__ import annotations

from device.app.controller import DeviceController
from device.app.enrol import EnrolmentFlow
from tests.device.helpers import TEACHER, enter_pin, wait
from tests.device.test_enrol_flow import FakeClient, FakeSyncer


def _start_session(device: DeviceController) -> None:
    device.startSession()
    assert device.screen == "start" and device.startTitle == "Choose the course"
    device.startNext()  # course -> section
    device.startNext()  # section -> period (templates exist, so no blocker)
    assert device.startTitle == "Choose the period"
    device.startNext()  # start -> asks for the teacher PIN
    enter_pin(device, "1234")


def test_full_session_records_events_and_restores_cooldown_on_resume(world):
    device, store, _ = world
    assert device.screen == "idle"
    assert "Not calibrated" in device.idleWarning

    _start_session(device)
    assert device.screen == "live" and device.session is not None
    assert device.totalCount == 3
    assert "22EC71 VLSI" in device.liveHeader and "NOT CALIBRATED" in device.liveHeader

    wait(lambda: store.pending_count() >= 1)
    assert store.pending_count() == 1
    event = store.pending_events()[0]
    assert event["usn"] == "1BM22EC001" and event["session_uuid"] == device.session.uuid
    wait(lambda: "Already marked" in device.bannerText, timeout_ms=4000)
    assert device.bannerTone == "warn"
    assert store.pending_count() == 1  # cooldown: the same face is not recorded twice
    assert device.presentCount == 1 and "Aditi" in device.recentText

    # Simulate a crash: back on Idle over the same store, the session can be resumed.
    session_uuid = device.session.uuid
    device.session = None
    device.show_idle()
    assert device.resumable
    device.resumeSession()
    enter_pin(device, "1234")
    assert device.screen == "live" and device.session.uuid == session_uuid
    assert device.presentCount == 1
    wait(lambda: "Already marked" in device.bannerText, timeout_ms=4000)
    assert store.pending_count() == 1

    device.endSession()
    enter_pin(device, "1234")
    assert device.screen == "idle" and device.session is None
    assert store.get_session(session_uuid)["ended_at"] is not None
    assert not device.resumable


def test_match_card_carries_name_and_usn(world):
    device, _, _ = world
    _start_session(device)
    wait(lambda: device.bannerTone == "ok")
    assert device.bannerText.startswith("Face matched – Aditi (1BM22EC001)")
    assert device.bannerTitle == "Aditi" and device.bannerSubtitle.startswith("1BM22EC001")


def test_wrong_pin_stays_and_cancel_goes_back(world):
    device, store, _ = world
    device.startSession()
    device.startNext()
    device.startNext()
    device.startNext()
    errors = device.pinErrors
    enter_pin(device, "0000")
    assert device.screen == "pin" and device.pinLength == 0
    assert device.pinErrors == errors + 1 and "not recognised" in device.pinMessage
    device.pinCancel()
    assert device.screen == "start"  # back where the teacher was
    assert store.get_open_session() is None if hasattr(store, "get_open_session") else True


def test_settings_need_an_admin_pin(world):
    device, _, _ = world
    device.openSettings()
    enter_pin(device, "1234")  # a teacher is not enough
    assert device.screen == "pin" and "Admin" in device.pinMessage
    for _ in range(4):
        device.pinBackspace()
    enter_pin(device, "9999")
    assert device.screen == "settings"


def test_start_is_blocked_for_a_section_without_templates(world):
    device, store, _ = world
    store.put_catalog("sections", [{"id": 1, "name": "ECE-7A"}, {"id": 2, "name": "ECE-7B"}])
    store.put_catalog("offerings", [{"id": 1, "course_id": 1, "section_id": 2, "faculty_id": 7}])
    device.startSession()
    device.startNext()  # course
    device.startNext()  # section ECE-7B has no templates -> blocked
    assert device.screen == "start" and device.startStep == 1
    assert "No faces enrolled" in device.startHint
    device.startBack()
    device.startBack()
    assert device.screen == "idle"


def test_settings_shows_fallback_thresholds_and_test_mode_records_nothing(world):
    device, store, _ = world
    device.openSettings()
    enter_pin(device, "9999")
    values = {row["key"]: row["value"] for row in device.settingsRows}
    assert "fallback" in values["Threshold"] and "NOT CALIBRATED" in device.settingsWarning
    device.show_idle()
    device.testRecognition()
    assert device.screen == "live" and device.endLabel == "Back"
    wait(lambda: "Face matched" in device.bannerText or "Already marked" in device.bannerText)
    assert store.pending_count() == 0
    device.endSession()  # "Back" outside a session needs no PIN
    assert device.screen == "idle"


def test_live_frames_reach_the_video_sink_only_on_live(world):
    device, _, _ = world
    wait(lambda: False, 300)
    assert device.videoSink.image().isNull()  # idle: no preview work
    device.testCamera()
    wait(lambda: not device.videoSink.image().isNull())
    image = device.videoSink.image()
    assert image.width() <= device.config.camera.preview_size[0]  # downsized once
    device.show_idle()
    assert device.videoSink.image().isNull()


def test_pipeline_sleeps_unless_a_screen_shows_video(world):
    device, _, worker = world
    assert device.screen == "idle" and worker.paused  # no inference on the idle screen
    device.testCamera()
    assert not worker.paused
    seen = worker.frames_seen
    wait(lambda: worker.frames_seen > seen + 3)
    device.openSettings()  # PIN screen: camera not shown
    assert device.screen == "pin" and worker.paused
    device.show_idle()
    assert worker.paused
    wait(lambda: False, 100)  # a frame already in flight may still finish
    seen = worker.frames_seen
    wait(lambda: False, 300)
    assert worker.frames_seen == seen


def test_enrolment_runs_consent_capture_upload_and_closes(world):
    device, store, _ = world
    store.set_setting("consent_version", "2026-11-v1")
    store.set_setting("consent_notice", "the notice")
    client, syncer = FakeClient(), FakeSyncer()
    flow = EnrolmentFlow(client, store, syncer)  # type: ignore[arg-type]
    device.start_enrol(flow, TEACHER)
    assert device.screen == "enrol" and device.enrolPage == "pick"
    assert [s["usn"] for s in device.enrolRoster] == ["1BM22EC001", "1BM22EC002"]
    device.enrolKey("2")
    assert [s["usn"] for s in device.enrolRoster] == ["1BM22EC002"]
    device.enrolBackspace()

    device.enrolChoose("1BM22EC001")
    assert device.enrolPage == "consent" and device.enrolNotice == "the notice"
    device.enrolBack()  # decline -> back to the list, nothing kept
    assert device.enrolPage == "pick"
    device.enrolChoose("1BM22EC001")
    device.enrolConsent()
    assert device.enrolPage == "capture" and device.enrolCaptureStatus == "Capture 1 of 3"

    wait(lambda: device.enrolPage == "done", timeout_ms=8000)
    assert device.enrolDoneTitle == "Enrolled" and not device.enrolCanRetry
    assert len(client.uploads) == 1 and client.uploads[0][0] == "1BM22EC001"
    device.enrolClose()
    assert device.screen == "idle"


def test_enrolment_rejected_offers_retry(world):
    device, store, _ = world
    store.set_setting("consent_version", "2026-11-v1")
    store.set_setting("consent_notice", "the notice")
    flow = EnrolmentFlow(FakeClient(reject="Student withdrew consent."), store, FakeSyncer())  # type: ignore[arg-type]
    device.start_enrol(flow, TEACHER)
    device.enrolChoose("1BM22EC001")
    device.enrolConsent()
    wait(lambda: device.enrolPage == "done", timeout_ms=8000)
    assert device.enrolDoneTitle == "Enrolment refused" and device.enrolCanRetry
    device.enrolRetry()
    assert device.enrolPage == "consent"


def test_a_held_match_is_not_replaced_by_already_marked(world):
    device, _, _ = world
    device.testCamera()  # outcomes suppressed: only our banners below
    device.show_banner("Face matched – A (1)", "ok", hold_ms=2500)
    device.show_banner("Already marked – A", "warn", hold_ms=1500)  # same face, next frame
    device.show_banner("Move closer", "neutral")
    assert device.bannerTone == "ok"  # still green
    device.show_banner("Face matched – B (2)", "ok", hold_ms=2500)  # another student: replaces
    assert device.bannerText == "Face matched – B (2)"


def test_last_sync_tile_is_short():
    from datetime import UTC, datetime

    from device.app.controller import _short_time

    now = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
    assert _short_time(None) == "never"
    assert len(_short_time("2026-11-11T10:30:00+00:00", now=now)) == 5  # HH:MM today
    assert _short_time("2026-11-02T10:30:00+00:00", now=now) == "02 Nov"
