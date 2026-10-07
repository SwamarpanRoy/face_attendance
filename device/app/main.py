"""Device app entry point.

    python -m device.app.main --sim                 # laptop: USB webcam in a window
    python -m device.app.main --sim --image path    # replay a still image (tests, no camera)
    python -m device.app.main --debug               # also listen for debugpy on 127.0.0.1:5678
    python -m device.app.main --selftest            # camera, models, DB, server checks; exit code
    python -m device.app.main --sync                # pull catalog + templates, then exit

Screens: Idle -> Start session (course, section, period, teacher PIN) -> Live ->
End session (PIN). Enrol (teacher PIN) and Settings (admin PIN) from Idle. Everything
works offline once the device has synced; the sync thread uploads the outbox, sends
heartbeats and prefetches templates for all assigned sections.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from common.face.engine import FaceEngine, ModelsMissingError
from common.schemas import HeartbeatIn
from common.version import __version__
from device.app import sysinfo
from device.app.camera import CameraError, make_source
from device.app.client import ServerClient
from device.app.config import DeviceConfig
from device.app.logging_setup import configure_logging
from device.app.store import DeviceStore

log = logging.getLogger("device")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Face attendance device app")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="device.toml (default: ~/.config/attendance/device.toml)",
    )
    parser.add_argument(
        "--sim", action="store_true", help="laptop simulator: OpenCV webcam, normal window"
    )
    parser.add_argument(
        "--image", type=Path, default=None, help="use a still image or folder instead of a camera"
    )
    parser.add_argument("--debug", action="store_true", help="listen for debugpy on 127.0.0.1:5678")
    parser.add_argument(
        "--selftest", action="store_true", help="check camera, models, DB and server, then exit"
    )
    parser.add_argument("--fullscreen", action="store_true", help="fullscreen (Pi touch display)")
    parser.add_argument(
        "--enrol-test",
        metavar="USN:NAME",
        default=None,
        help="store the first good embedding as a template for USN (simulator helper)",
    )
    parser.add_argument(
        "--exit-after", type=float, default=None, help="quit after N seconds (smoke tests)"
    )
    parser.add_argument(
        "--sync", action="store_true", help="pull catalog + templates from the server, then exit"
    )
    parser.add_argument(
        "--full-sync", action="store_true", help="with --sync: replace the whole template cache"
    )
    return parser


def start_debugpy() -> None:
    import debugpy

    debugpy.listen(("127.0.0.1", 5678))
    log.info("debugpy listening on 127.0.0.1:5678 (attach from VS Code through the SSH tunnel)")


def network_info() -> dict[str, str | None]:
    return {"ssid": sysinfo.ssid(), "ip": sysinfo.ip_address()}


def heartbeat_payload(config: DeviceConfig, store: DeviceStore) -> HeartbeatIn:
    return HeartbeatIn(
        device_id=config.device.id,
        app_version=__version__,
        ip=sysinfo.ip_address(),
        ssid=sysinfo.ssid(),
        queue_len=store.pending_count(),
        cpu_temp_c=sysinfo.cpu_temp_c(),
        free_mem_mb=sysinfo.free_mem_mb(),
        model_version=config.recognition.model_version,
        clock_synced=sysinfo.clock_synced(),
    )


def selftest(config: DeviceConfig, args: argparse.Namespace) -> int:
    """Print PASS/FAIL per subsystem; exit code 1 if anything failed."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: object) -> None:
        try:
            detail = fn()  # type: ignore[operator]
            results.append((name, True, str(detail)))
        except Exception as exc:
            results.append((name, False, f"{type(exc).__name__}: {exc}"))

    check("config", lambda: str(config.source_path or "defaults"))

    def models() -> str:
        t = time.perf_counter()
        FaceEngine(config.models_dir, config.engine_config())
        return f"loaded from {config.models_dir} in {(time.perf_counter() - t) * 1000:.0f} ms"

    check("models", models)

    def camera() -> str:
        source = make_source(
            sim=args.sim,
            webcam_index=config.camera.sim_webcam_index,
            size=config.camera.main_size,
            image=args.image,
        )
        source.start()
        try:
            frame = source.read()
            if frame is None:
                raise CameraError("no frame")
            return f"{source.description}: frame {frame.shape[1]}x{frame.shape[0]}"
        finally:
            source.stop()

    check("camera", camera)

    def database() -> str:
        store = DeviceStore(config.db_path)
        counts = store.counts()
        store.close()
        return (
            f"{config.db_path}: {counts.templates} templates, "
            f"{counts.pending_events} pending events"
        )

    check("database", database)

    def server() -> str:
        if not config.server.configured:
            raise RuntimeError("server url/token not configured in device.toml")
        client = ServerClient(config.server.url, config.server.token, config.server.timeout_s)
        try:
            status = client.health()
            if not status.reachable:
                raise RuntimeError(status.detail)
            return f"{config.server.url} {status.detail}"
        finally:
            client.close()

    check("server", server)
    check(
        "clock",
        lambda: "NTP synced" if sysinfo.clock_synced() else "NOT synced (events will be flagged)",
    )

    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")  # noqa: T201
    return 0 if all(ok for _, ok, _ in results) else 1


def sync_now(config: DeviceConfig, *, full: bool) -> int:
    """Pull catalog and templates once (what Settings > Sync now does)."""
    from device.app.sync import ModelMismatchError, Syncer

    if not config.server.configured:
        log.error("server url/token not configured in %s", config.source_path or "device.toml")
        return 2
    store = DeviceStore(config.db_path)
    client = ServerClient(config.server.url, config.server.token, config.server.timeout_s)
    try:
        result = Syncer(store, client, config.recognition.model_version).pull_all(full=full)
    except ModelMismatchError as exc:
        log.error("%s", exc)
        return 3
    finally:
        client.close()
        store.close()
    print(  # noqa: T201
        f"synced: full={result.full} students={result.students} templates={result.templates} "
        f"deleted={result.deleted} calibrated={result.calibrated}"
    )
    return 0


def run_app(config: DeviceConfig, args: argparse.Namespace) -> int:
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    from device.app.feedback import Feedback
    from device.app.pins import PinVerifier
    from device.app.pipeline import PipelineWorker
    from device.app.session import SessionManager
    from device.app.ui.main_window import EXIT_RESTART, MainWindow

    try:
        engine = FaceEngine(config.models_dir, config.engine_config())
    except ModelsMissingError as exc:
        log.error("%s", exc)
        return 2
    store = DeviceStore(config.db_path)
    camera = make_source(
        sim=args.sim,
        webcam_index=config.camera.sim_webcam_index,
        size=config.camera.main_size,
        image=args.image,
    )
    client = (
        ServerClient(config.server.url, config.server.token, config.server.timeout_s)
        if config.server.configured
        else None
    )
    feedback = Feedback(config.ui)
    sessions = SessionManager(store, config, clock_synced=sysinfo.clock_synced)
    exit_code = {"code": 0}

    app = QApplication(sys.argv[:1])
    worker = PipelineWorker(camera, engine, _empty_recognizer(config))

    def request_restart() -> None:
        exit_code["code"] = EXIT_RESTART

    window = MainWindow(
        config=config,
        store=store,
        worker=worker,
        sessions=sessions,
        client=client,
        verifier=PinVerifier(store),
        network_info=network_info,
        clock_synced=sysinfo.clock_synced,
        heartbeat_payload=(lambda: heartbeat_payload(config, store)) if client else None,
        on_matched_feedback=lambda outcome: feedback.matched(outcome.name or ""),
        on_restart=request_restart,
        show_capture_button=bool(args.sim and args.enrol_test),
        fullscreen=args.fullscreen,
    )

    if args.enrol_test:
        usn, _, name = args.enrol_test.partition(":")
        usn = usn.strip().upper()
        name = name.strip() or usn

        def store_test(capture: object) -> None:
            window.store_test_template(capture.embedding, usn, name)  # type: ignore[attr-defined]
            log.info("stored test template for %s (%s)", usn, name)
            window.begin_test_mode()

        worker.capture_ready.connect(store_test)
        worker.request_capture()
    window.showFullScreen() if args.fullscreen else window.show()
    window.start_workers()
    # Qt's event loop runs in C++, so Ctrl+C (SIGINT) or pkill/deploy (SIGTERM) would
    # otherwise land as KeyboardInterrupt inside a frame slot and abort the process
    # without stopping the camera and sync threads. Close the window instead (same path
    # as the close button); the timer gives Python a chance to run the handler.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: window.close())
    signal_wakeup = QTimer()
    signal_wakeup.timeout.connect(lambda: None)
    signal_wakeup.start(250)
    if args.exit_after:
        QTimer.singleShot(int(args.exit_after * 1000), window.close)

    code = app.exec_()
    worker.stop()
    if client is not None:
        client.close()
    store.close()
    log.info("device app exited with %d after %d frames", code, worker.frames_seen)
    return exit_code["code"] or int(code)


def _empty_recognizer(config: DeviceConfig):  # noqa: ANN202 - internal helper
    from common.face.matcher import Recognizer, TemplateIndex

    return Recognizer(TemplateIndex([]), config.match_config())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DeviceConfig.load(args.config)
    configure_logging(config.logging.level, config.log_path)
    log.info("face-attendance device %s starting (sim=%s)", __version__, args.sim)
    if args.debug:
        start_debugpy()
    if args.selftest:
        return selftest(config, args)
    if args.sync or args.full_sync:
        return sync_now(config, full=args.full_sync)
    return run_app(config, args)


if __name__ == "__main__":
    raise SystemExit(main())
