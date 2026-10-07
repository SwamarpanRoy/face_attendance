"""Touch-friendly number pad and the PIN dialog built on it (no keyboard needed)."""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from device.app.pins import FacultyRef, PinLockedError, PinVerifier

BUTTON_HEIGHT = 64


class NumberPad(QWidget):
    """3x4 grid of big digit buttons; emits the growing text."""

    changed = pyqtSignal(str)
    submitted = pyqtSignal(str)

    def __init__(self, *, max_length: int = 12, masked: bool = False) -> None:
        super().__init__()
        self.max_length = max_length
        self._text = ""
        self.display = QLineEdit()
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setFont(QFont("Sans", 22, QFont.Bold))
        self.display.setMinimumHeight(56)
        if masked:
            self.display.setEchoMode(QLineEdit.Password)

        grid = QGridLayout()
        labels = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"]
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setMinimumHeight(BUTTON_HEIGHT)
            button.setFont(QFont("Sans", 18))
            button.clicked.connect(lambda _checked, value=label: self._press(value))
            grid.addWidget(button, index // 3, index % 3)
        layout = QVBoxLayout()
        layout.addWidget(self.display)
        layout.addLayout(grid)
        self.setLayout(layout)

    @property
    def text(self) -> str:
        return self._text

    def clear(self) -> None:
        self._text = ""
        self.display.setText("")
        self.changed.emit("")

    def _press(self, value: str) -> None:
        if value == "OK":
            self.submitted.emit(self._text)
            return
        if value == "⌫":
            self._text = self._text[:-1]
        elif len(self._text) < self.max_length:
            self._text += value
        self.display.setText(self._text)
        self.changed.emit(self._text)


class PinDialog(QDialog):
    """Ask for a PIN and resolve it to a faculty member via ``PinVerifier``."""

    def __init__(
        self,
        verifier: PinVerifier,
        *,
        title: str,
        require_admin: bool = False,
        on_online_fallback: Callable[[str], FacultyRef | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.verifier = verifier
        self.require_admin = require_admin
        self._fallback = on_online_fallback
        self.result_ref: FacultyRef | None = None

        self.heading = QLabel(title)
        self.heading.setFont(QFont("Sans", 16, QFont.Bold))
        self.heading.setAlignment(Qt.AlignCenter)
        self.message = QLabel("Enter your PIN")
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.pad = NumberPad(max_length=12, masked=True)
        self.pad.submitted.connect(self._submit)
        cancel = QPushButton("Cancel")
        cancel.setMinimumHeight(BUTTON_HEIGHT)
        cancel.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(cancel)
        layout = QVBoxLayout()
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        layout.addWidget(self.pad)
        layout.addLayout(row)
        self.setLayout(layout)
        self.resize(360, 560)

    def _submit(self, pin: str) -> None:
        if not pin:
            return
        try:
            ref = self.verifier.verify(pin)
        except PinLockedError as exc:
            self.message.setText(str(exc))
            self.pad.clear()
            return
        if ref is None and self._fallback is not None and not self.verifier.has_pins:
            ref = self._fallback(pin)
        if ref is None:
            self.message.setText("PIN not recognised. Try again.")
            self.pad.clear()
            return
        if self.require_admin and not ref.is_admin:
            self.message.setText("Admin PIN required for this screen.")
            self.pad.clear()
            return
        self.result_ref = ref
        self.accept()

    @classmethod
    def ask(
        cls,
        verifier: PinVerifier,
        *,
        title: str,
        require_admin: bool = False,
        parent: QWidget | None = None,
        on_online_fallback: Callable[[str], FacultyRef | None] | None = None,
    ) -> FacultyRef | None:
        dialog = cls(
            verifier,
            title=title,
            require_admin=require_admin,
            parent=parent,
            on_online_fallback=on_online_fallback,
        )
        return dialog.result_ref if dialog.exec_() == QDialog.Accepted else None
