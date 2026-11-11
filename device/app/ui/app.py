"""Load the Qt Quick UI: bundled fonts, the video item and ``main.qml``.

Kept separate from :mod:`device.app.controller` so the controller can be tested without
QML, and the QML can be smoke-tested without a camera.
"""

from __future__ import annotations

import functools
from pathlib import Path

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QFont, QFontDatabase, QGuiApplication
from PyQt5.QtQml import QQmlApplicationEngine, qmlRegisterType

from device.app.controller import DeviceController
from device.app.ui.video import VideoSurface

QML_DIR = Path(__file__).resolve().parent / "qml"


@functools.cache
def _register_types() -> None:
    """Once per process: QML may only learn a type name once."""
    qmlRegisterType(VideoSurface, "Device", 1, 0, "VideoSurface")


def load_fonts(app: QGuiApplication) -> None:
    """Bundle Inter so the Pi and the laptop render the same text (OFL, see fonts/)."""
    for ttf in sorted((QML_DIR / "fonts").glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))
    app.setFont(QFont("Inter", 11))


def load_ui(
    engine: QQmlApplicationEngine,
    controller: DeviceController,
    *,
    fullscreen: bool = False,
    visible: bool = True,
) -> None:
    """Expose the controller to QML as ``device`` and open the window."""
    app = QGuiApplication.instance()
    assert isinstance(app, QGuiApplication), "create a QGuiApplication first"
    load_fonts(app)
    _register_types()
    context = engine.rootContext()
    context.setContextProperty("device", controller)
    context.setContextProperty("uiVisible", visible)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "main.qml")))
    if not engine.rootObjects():
        raise RuntimeError("the QML UI failed to load (see the QML errors above)")
    if fullscreen and visible:
        # Set here, not bound in QML: binding `visibility` at load conflicts with `visible`.
        engine.rootObjects()[0].showFullScreen()
