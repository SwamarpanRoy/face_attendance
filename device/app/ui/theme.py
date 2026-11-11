"""Dark glass look for the whole device UI: aurora backdrop, translucent panels, frost.

Why: the device sits in bright classrooms and the camera preview is the hero of the Live
screen. A dark backdrop with soft colour fields keeps glare low on a small glossy panel,
translucent "glass" panels with a lit top edge give depth without heavy chrome, and one
status colour (green match, amber already-marked, red not-recognised) carries meaning.

Qt has no backdrop blur, so the glass is built two cheap ways: on static screens the
backdrop is already soft (radial colour fields), so translucent panels over it read as
frosted with no per-frame work (the backdrop is painted once per size and cached by the
caller); over the live camera, :func:`frost` really blurs the video strip behind each
floating panel (downscale-blur-upscale, a few ms on a Pi 5).

Widgets only declare *what* they are through dynamic properties (``variant`` on buttons,
``role`` and ``tone`` on labels and frames) and this module decides how that looks, so
colours live in one place. Sizes are set by the screens (pixel fonts via :func:`font`)
because the layout is measured to fit a 480x800 panel. Token names follow shadcn/ui.
"""

from __future__ import annotations

import cv2
import numpy as np
from PyQt5.QtCore import QPointF, QSize
from PyQt5.QtGui import QColor, QFont, QPainter, QPalette, QPixmap, QRadialGradient
from PyQt5.QtWidgets import QApplication, QWidget

from common.face.types import Array

COLORS = {
    "background": "#07070d",
    "foreground": "#fafafa",
    "muted": "#c4c4cc",
    "subtle": "#8b8b99",
    "primary_foreground": "#0b0b14",
    "ok": "#6ee7b7",
    "warn": "#fcd34d",
    "error": "#fca5a5",
}
_C = COLORS

# Glass recipe: translucent white fill, hairline border, brighter top edge (light catching
# the rim). Tints are the same recipe in a status colour.
_GLASS = (
    "background: rgba(255, 255, 255, 0.08);"
    " border: 1px solid rgba(255, 255, 255, 0.14);"
    " border-top: 1px solid rgba(255, 255, 255, 0.30);"
)
_GLASS_STRONG = (
    "background: rgba(255, 255, 255, 0.14);"
    " border: 1px solid rgba(255, 255, 255, 0.20);"
    " border-top: 1px solid rgba(255, 255, 255, 0.40);"
)


def _tint(rgb: str, fill: float, edge: float) -> str:
    return (
        f"background: rgba({rgb}, {fill});"
        f" border: 1px solid rgba({rgb}, {edge});"
        f" border-top: 1px solid rgba(255, 255, 255, 0.35);"
    )


_OK, _WARN, _ERR = "16, 185, 129", "245, 158, 11", "239, 68, 68"

STYLESHEET = f"""
QWidget {{ color: {_C["foreground"]}; }}
QStackedWidget, QStackedWidget > QWidget {{ background: transparent; }}

/* ---------------------------------------------------------------- buttons */
QPushButton {{ {_GLASS} border-radius: 18px; padding: 0 16px; color: {_C["foreground"]}; }}
QPushButton:pressed {{ background: rgba(255, 255, 255, 0.18); }}
QPushButton:disabled {{ color: {_C["subtle"]}; }}
QPushButton[variant="primary"] {{
    background: rgba(255, 255, 255, 0.92);
    color: {_C["primary_foreground"]};
    border: 1px solid rgba(255, 255, 255, 0.95);
    border-radius: 22px;
}}
QPushButton[variant="primary"]:pressed {{ background: rgba(255, 255, 255, 0.72); }}
QPushButton[variant="accent"] {{ {_tint(_OK, 0.55, 0.7)} border-radius: 22px; color: #ffffff; }}
QPushButton[variant="destructive"] {{
    {_tint(_ERR, 0.18, 0.45)} border-radius: 20px; color: {_C["error"]};
}}
QPushButton[variant="destructive"]:pressed {{ background: rgba({_ERR}, 0.32); }}
QPushButton[variant="ghost"] {{ background: transparent; border: none; color: {_C["muted"]}; }}
QPushButton[variant="ghost"]:pressed {{ color: {_C["foreground"]}; }}
QPushButton[variant="key"] {{ {_GLASS} border-radius: 24px; }}

/* ---------------------------------------------------------------- surfaces */
QFrame[role="card"] {{ {_GLASS} border-radius: 20px; }}
QFrame[role="glass"] {{ {_GLASS_STRONG} border-radius: 20px; }}
QFrame[role="card"] QLabel, QFrame[role="glass"] QLabel {{
    background: transparent; border: none;
}}
QScrollArea[role="plain"], QScrollArea[role="plain"] > QWidget > QWidget {{
    background: transparent; border: none;
}}
QFrame[role="step"] {{ background: rgba(255, 255, 255, 0.16); border-radius: 2px; }}
QFrame[role="step"][tone="active"] {{ background: {_C["foreground"]}; }}

/* ---------------------------------------------------------------- text */
QLabel[role="muted"] {{ color: {_C["muted"]}; }}
QLabel[role="subtle"] {{ color: {_C["subtle"]}; }}
QLabel[role="eyebrow"] {{ color: {_C["subtle"]}; font-weight: 600; }}
QLabel[role="danger"] {{ color: {_C["error"]}; }}
QLabel[role="caption"] {{ color: {_C["subtle"]}; font-size: 10px; }}

QLabel[role="badge"] {{
    {_GLASS} border-radius: 14px; padding: 5px 12px; font-weight: 600; color: {_C["muted"]};
}}
QLabel[role="badge"][tone="ok"] {{ {_tint(_OK, 0.20, 0.55)} color: {_C["ok"]}; }}
QLabel[role="badge"][tone="warn"] {{ {_tint(_WARN, 0.20, 0.55)} color: {_C["warn"]}; }}
QLabel[role="badge"][tone="error"] {{ {_tint(_ERR, 0.20, 0.55)} color: {_C["error"]}; }}

QLabel[role="alert"] {{ border-radius: 16px; padding: 10px 14px; font-weight: 600; }}
QLabel[role="alert"][tone="warn"] {{ {_tint(_WARN, 0.14, 0.40)} color: {_C["warn"]}; }}
QLabel[role="alert"][tone="error"] {{ {_tint(_ERR, 0.14, 0.40)} color: {_C["error"]}; }}

/* Result card floating over the (frosted) video. */
QLabel[role="banner"] {{ border-radius: 22px; padding: 14px 18px; color: #ffffff; }}
QLabel[role="banner"][tone="neutral"] {{ {_GLASS_STRONG} }}
QLabel[role="banner"][tone="ok"] {{ {_tint(_OK, 0.62, 0.85)} }}
QLabel[role="banner"][tone="warn"] {{ {_tint(_WARN, 0.62, 0.85)} }}
QLabel[role="banner"][tone="error"] {{ {_tint(_ERR, 0.62, 0.85)} }}

QLabel[role="video"] {{ background: #000000; color: {_C["subtle"]}; }}
QLabel[role="video"][tone="rounded"] {{ border-radius: 22px; }}

/* ---------------------------------------------------------------- lists */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{
    {_GLASS} border-radius: 18px; padding: 16px 18px; margin: 0 0 8px 0;
    color: {_C["foreground"]};
}}
QListWidget::item:selected {{
    background: rgba(255, 255, 255, 0.92);
    color: {_C["primary_foreground"]};
    border: 1px solid rgba(255, 255, 255, 0.95);
}}

/* ---------------------------------------------------------------- inputs */
QLineEdit {{ {_GLASS} border-radius: 16px; padding: 8px 12px; }}
QLineEdit[role="pin"] {{ background: transparent; border: none; }}
QTextEdit, QPlainTextEdit, QTextBrowser {{ {_GLASS} border-radius: 18px; padding: 12px; }}
QComboBox {{ {_GLASS} border-radius: 14px; padding: 8px 12px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{
    background: #16161f; border: 1px solid rgba(255, 255, 255, 0.14);
    selection-background-color: rgba(255, 255, 255, 0.92);
    selection-color: {_C["primary_foreground"]};
}}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 0.25); border-radius: 3px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""

# Soft colour fields of the backdrop: (centre x, centre y as a fraction of the size,
# radius as a fraction of the larger side, RGBA at the centre).
_AURORA = (
    (0.10, 0.08, 0.75, (124, 58, 237, 150)),  # violet, top left
    (0.95, 0.45, 0.70, (6, 182, 212, 110)),  # cyan, right
    (0.20, 0.95, 0.80, (236, 72, 153, 95)),  # pink, bottom left
    (0.80, 1.05, 0.55, (59, 130, 246, 110)),  # blue, bottom right
)


def backdrop(size: QSize) -> QPixmap:
    """The aurora background at ``size``; callers cache it and repaint only on resize."""
    pixmap = QPixmap(max(size.width(), 1), max(size.height(), 1))
    pixmap.fill(QColor(_C["background"]))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    longest = max(pixmap.width(), pixmap.height())
    for fx, fy, fr, (r, g, b, a) in _AURORA:
        centre = QPointF(fx * pixmap.width(), fy * pixmap.height())
        gradient = QRadialGradient(centre, fr * longest)
        gradient.setColorAt(0.0, QColor(r, g, b, a))
        gradient.setColorAt(1.0, QColor(r, g, b, 0))
        painter.fillRect(pixmap.rect(), gradient)
    painter.end()
    return pixmap


def paint_backdrop(widget: QWidget, cache: dict[str, QPixmap]) -> None:
    """Paint the cached backdrop behind ``widget`` (call from its paintEvent)."""
    current = cache.get("pixmap")
    if current is None or current.size() != widget.size():
        current = backdrop(widget.size())
        cache["pixmap"] = current
    painter = QPainter(widget)
    painter.drawPixmap(0, 0, current)
    painter.end()


def frost(image: Array, x: int, y: int, w: int, h: int, *, radius: int) -> None:
    """Blur ``image[y:y+h, x:x+w]`` in place inside a rounded rectangle (frosted glass).

    Downscale, blur, upscale: a strong blur for a few ms on a Pi 5, which is what lets
    the floating panels over the live camera look like real glass.
    """
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, image.shape[1]), min(y + h, image.shape[0])
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    region = image[y0:y1, x0:x1]
    rh, rw = region.shape[:2]
    small = cv2.resize(region, (max(rw // 8, 1), max(rh // 8, 1)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 1.5)
    blurred = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_LINEAR)
    mask = np.zeros((rh, rw), np.uint8)
    r = max(min(radius, rw // 2, rh // 2), 0)
    cv2.rectangle(mask, (r, 0), (rw - 1 - r, rh - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (rw - 1, rh - 1 - r), 255, -1)
    for cx, cy in ((r, r), (rw - 1 - r, r), (r, rh - 1 - r), (rw - 1 - r, rh - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
    region[:] = (blurred * alpha + region * (1.0 - alpha)).astype(np.uint8)


def font(px: int, weight: int = QFont.Normal) -> QFont:
    """Pixel-sized font, so text is the same size on any panel DPI."""
    result = QFont("Sans")
    result.setPixelSize(px)
    result.setWeight(weight)
    return result


def _palette() -> QPalette:
    """Dark palette for what the stylesheet does not reach (popups, selections)."""
    palette = QPalette()
    for role, color in (
        (QPalette.Window, _C["background"]),
        (QPalette.WindowText, _C["foreground"]),
        (QPalette.Base, "#16161f"),
        (QPalette.AlternateBase, "#1f1f2b"),
        (QPalette.Text, _C["foreground"]),
        (QPalette.Button, "#16161f"),
        (QPalette.ButtonText, _C["foreground"]),
        (QPalette.Highlight, "#f4f4f5"),
        (QPalette.HighlightedText, _C["primary_foreground"]),
        (QPalette.ToolTipBase, "#16161f"),
        (QPalette.ToolTipText, _C["foreground"]),
        (QPalette.PlaceholderText, _C["subtle"]),
    ):
        palette.setColor(role, QColor(color))
    return palette


def apply(app: QApplication) -> None:
    """Install the theme for every window and dialog the app creates."""
    app.setStyle("Fusion")  # same rendering on the Pi and the laptop simulator
    app.setPalette(_palette())
    app.setStyleSheet(STYLESHEET)


def set_props(widget: QWidget, **props: str) -> None:
    """Set dynamic properties and re-apply the stylesheet (Qt does not on its own)."""
    for name, value in props.items():
        widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
