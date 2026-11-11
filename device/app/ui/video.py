"""Camera frames into Qt Quick: a sink the controller fills and an item that draws it.

The pipeline runs on its own thread and hands frames to the controller (Qt thread),
which annotates and downsizes them once and puts the result in a :class:`VideoSink`.
Every :class:`VideoSurface` bound to that sink repaints when a new frame arrives,
filling its area and cropping the overflow (full-bleed, no black bars), so the QML
glass panels on top can blur real video behind them on the GPU.
"""

from __future__ import annotations

from PyQt5.QtCore import QObject, QRectF, pyqtProperty, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter
from PyQt5.QtQuick import QQuickItem, QQuickPaintedItem


class VideoSink(QObject):
    """Holds the latest preview frame; never stores more than one, never writes to disk."""

    frameChanged = pyqtSignal()
    messageChanged = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._message = "Starting camera…"

    def image(self) -> QImage:
        return self._image

    def set_image(self, image: QImage) -> None:
        self._image = image
        if self._message:
            self._message = ""
            self.messageChanged.emit()
        self.frameChanged.emit()

    def set_message(self, message: str) -> None:
        """Text shown instead of video (camera starting, camera error)."""
        self._message = message
        self.messageChanged.emit()

    @pyqtProperty(str, notify=messageChanged)
    def message(self) -> str:
        return self._message

    def clear(self) -> None:
        self._image = QImage()
        self.frameChanged.emit()


class VideoSurface(QQuickPaintedItem):
    """QML item ``VideoSurface { sink: device.video }``: draws the sink's frame, fill + crop."""

    sinkChanged = pyqtSignal()

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self._sink: VideoSink | None = None
        self.setOpaquePainting(True)

    # Typed QObject: QML only knows registered types, and the sink is handed over as the
    # controller's ``videoSink`` property.
    @pyqtProperty(QObject, notify=sinkChanged)
    def sink(self) -> VideoSink | None:
        return self._sink

    @sink.setter
    def sink(self, value: QObject | None) -> None:
        if value is not None and not isinstance(value, VideoSink):
            raise TypeError("VideoSurface.sink must be the controller's videoSink")
        if self._sink is not None:
            self._sink.frameChanged.disconnect(self.update)
        self._sink = value
        if value is not None:
            value.frameChanged.connect(self.update)
        self.sinkChanged.emit()
        self.update()

    def paint(self, painter: QPainter) -> None:
        width, height = self.width(), self.height()
        painter.fillRect(QRectF(0, 0, width, height), QColor(0, 0, 0))
        image = self._sink.image() if self._sink is not None else QImage()
        if image.isNull() or width <= 0 or height <= 0:
            return
        scale = max(width / image.width(), height / image.height())
        draw_w, draw_h = image.width() * scale, image.height() * scale
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(
            QRectF((width - draw_w) / 2, (height - draw_h) / 2, draw_w, draw_h), image
        )
