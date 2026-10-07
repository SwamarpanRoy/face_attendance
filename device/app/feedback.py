"""Optional audible feedback: a beep and/or spoken "Face matched" via espeak-ng.

Both are off by default and toggled in ``device.toml``. Speech runs as a detached
subprocess so the pipeline never waits on audio.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from device.app.config import UiSection

log = logging.getLogger(__name__)


class Feedback:
    def __init__(self, config: UiSection) -> None:
        self.config = config
        self._espeak = shutil.which("espeak-ng") if config.tts else None
        if config.tts and not self._espeak:
            log.warning("tts enabled but espeak-ng is not installed")

    def matched(self, name: str) -> None:
        if self.config.sound:
            try:
                from PyQt5.QtWidgets import QApplication

                QApplication.beep()
            except Exception:
                log.debug("beep unavailable")
        if self._espeak:
            try:
                subprocess.Popen(
                    [self._espeak, "-s", "170", f"Face matched, {name}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                log.warning("espeak-ng failed: %s", exc)
