"""Camera sources behind one small interface so the pipeline never knows the hardware.

* ``PiCamera2Source``: any Raspberry Pi camera through picamera2 (AI Camera / IMX500,
  Camera Module 3 / IMX708); continuous autofocus when the lens has it.
* ``OpenCVSource``: any USB webcam (the laptop simulator).
* ``ImageSource``: a still image or folder played in a loop, for tests, benchmarks and
  running the app on a Pi without a camera attached.

All sources return BGR ``uint8`` frames, which is what OpenCV and the detector expect.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from common.face.types import Array

log = logging.getLogger(__name__)


class CameraSource(Protocol):
    description: str

    def start(self) -> None: ...

    def read(self) -> Array | None: ...

    def stop(self) -> None: ...


class CameraError(RuntimeError):
    """The camera could not be opened or stopped delivering frames."""


class OpenCVSource:
    """USB webcam via OpenCV. DirectShow on Windows opens noticeably faster."""

    def __init__(self, index: int = 0, size: tuple[int, int] = (1280, 720)) -> None:
        self.index = index
        self.size = size
        self.description = f"webcam {index} via OpenCV"
        self._capture: cv2.VideoCapture | None = None

    def start(self) -> None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        capture = cv2.VideoCapture(self.index, backend)
        if not capture.isOpened():
            raise CameraError(f"could not open webcam {self.index}")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        self._capture = capture
        log.info("opened %s at %dx%d", self.description, capture.get(3), capture.get(4))

    def read(self) -> Array | None:
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        return frame if ok else None

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class ImageSource:
    """Replays one image (or every image in a folder) at a fixed frame rate."""

    def __init__(self, path: Path, fps: float = 15.0) -> None:
        self.path = path
        self.fps = fps
        self.description = f"image loop from {path}"
        self._frames: list[Array] = []
        self._index = 0
        self._last = 0.0

    def start(self) -> None:
        paths = sorted(self.path.glob("*")) if self.path.is_dir() else [self.path]
        for candidate in paths:
            image = cv2.imread(str(candidate))
            if image is not None:
                self._frames.append(image)
        if not self._frames:
            raise CameraError(f"no readable images at {self.path}")

    def read(self) -> Array | None:
        wait = 1.0 / self.fps - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        frame = self._frames[self._index % len(self._frames)]
        self._index += 1
        return frame.copy()

    def stop(self) -> None:
        self._frames.clear()


class PiCamera2Source:
    """Raspberry Pi camera via picamera2 (imported lazily: apt package, Pi only).

    picamera2's ``RGB888`` format delivers arrays in BGR byte order, i.e. exactly what
    OpenCV expects, so no channel swap is done here. Camera Module 3 gets continuous
    autofocus so students at different distances stay sharp; the AI Camera (IMX500) has
    a manual-focus lens, which must be set by hand once (blurry frames show "Hold still").
    The IMX500's on-sensor NPU is not used: inference stays in onnxruntime.
    """

    def __init__(self, size: tuple[int, int] = (1280, 960)) -> None:
        self.size = size
        self.description = f"Pi camera via picamera2 at {size[0]}x{size[1]}"
        self._camera: Any = None

    def start(self) -> None:
        try:
            from libcamera import controls  # type: ignore[import-not-found]
            from picamera2 import Picamera2  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - laptop
            raise CameraError("picamera2 is not installed; use --sim on a laptop") from exc
        camera = Picamera2()
        model = camera.camera_properties.get("Model", "unknown sensor")
        self.description = f"Pi camera ({model}) via picamera2 at {self.size[0]}x{self.size[1]}"
        config = camera.create_video_configuration(main={"size": self.size, "format": "RGB888"})
        camera.configure(config)
        camera.start()
        if "AfMode" in camera.camera_controls:
            try:
                camera.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            except Exception as exc:
                log.warning("continuous autofocus not available: %s", exc)
        else:
            log.info("%s has a fixed/manual-focus lens: focus it by hand once", model)
        self._camera = camera
        log.info("opened %s", self.description)

    def read(self) -> Array | None:
        if self._camera is None:
            return None
        frame = self._camera.capture_array("main")
        return np.asarray(frame)

    def stop(self) -> None:
        if self._camera is not None:
            self._camera.stop()
            self._camera.close()
            self._camera = None


def make_source(
    *, sim: bool, webcam_index: int, size: tuple[int, int], image: Path | None
) -> CameraSource:
    """Pick the camera for this run: still image > webcam (sim) > Pi camera."""
    if image is not None:
        return ImageSource(image)
    if sim:
        return OpenCVSource(webcam_index, size)
    return PiCamera2Source(size)
