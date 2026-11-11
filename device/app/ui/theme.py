"""Dark, camera-first look for the whole device UI (one Qt stylesheet + palette).

Why dark: the device sits in bright classrooms and the camera preview is the hero of the
Live screen; a near-black UI frames the video, keeps glare low on a small glossy panel
and lets one status colour (green match, amber already-marked, red not-recognised) carry
the meaning at a glance. The token names follow shadcn/ui (background, card, muted,
primary, destructive), translated to Qt because the device runs PyQt5, not a web stack.

Widgets only declare *what* they are through dynamic properties (``variant`` on buttons,
``role`` and ``tone`` on labels and frames) and this module decides how that looks, so
colours live in one place. Sizes are set by the screens (pixel fonts via :func:`font`)
because the layout is measured to fit a 480x800 panel.
"""

from __future__ import annotations

from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication, QWidget

COLORS = {
    "background": "#09090b",
    "card": "#18181b",
    "card_pressed": "#27272a",
    "border": "#27272a",
    "border_strong": "#3f3f46",
    "foreground": "#fafafa",
    "muted": "#a1a1aa",
    "subtle": "#71717a",
    "primary": "#fafafa",
    "primary_foreground": "#09090b",
    "primary_pressed": "#d4d4d8",
    "ok": "#34d399",
    "ok_solid": "#059669",
    "warn": "#fbbf24",
    "warn_solid": "#d97706",
    "error": "#f87171",
    "error_solid": "#dc2626",
}
_C = COLORS

STYLESHEET = f"""
QWidget {{ color: {_C["foreground"]}; }}
QMainWindow, QDialog, QStackedWidget > QWidget {{ background: {_C["background"]}; }}

/* ---------------------------------------------------------------- buttons */
QPushButton {{
    background: {_C["card"]};
    color: {_C["foreground"]};
    border: 1px solid {_C["border"]};
    border-radius: 16px;
    padding: 0 16px;
}}
QPushButton:pressed {{ background: {_C["card_pressed"]}; }}
QPushButton:disabled {{ color: {_C["subtle"]}; }}
QPushButton[variant="primary"] {{
    background: {_C["primary"]};
    color: {_C["primary_foreground"]};
    border: none;
    border-radius: 20px;
}}
QPushButton[variant="primary"]:pressed {{ background: {_C["primary_pressed"]}; }}
QPushButton[variant="accent"] {{
    background: {_C["ok_solid"]};
    color: #ffffff;
    border: none;
    border-radius: 20px;
}}
QPushButton[variant="destructive"] {{
    background: rgba(239, 68, 68, 0.14);
    color: {_C["error"]};
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 18px;
}}
QPushButton[variant="destructive"]:pressed {{ background: rgba(239, 68, 68, 0.28); }}
QPushButton[variant="ghost"] {{
    background: transparent;
    color: {_C["muted"]};
    border: none;
}}
QPushButton[variant="ghost"]:pressed {{ color: {_C["foreground"]}; }}
QPushButton[variant="key"] {{
    background: {_C["card"]};
    border: 1px solid {_C["border"]};
    border-radius: 22px;
}}

/* ---------------------------------------------------------------- surfaces */
QFrame[role="card"] {{
    background: {_C["card"]};
    border: 1px solid {_C["border"]};
    border-radius: 18px;
}}
QFrame[role="glass"] {{
    background: rgba(9, 9, 11, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 18px;
}}
QFrame[role="card"] QLabel, QFrame[role="glass"] QLabel {{ background: transparent; border: none; }}
QScrollArea[role="plain"], QScrollArea[role="plain"] > QWidget > QWidget {{
    background: transparent; border: none;
}}
QFrame[role="step"] {{ background: {_C["border_strong"]}; border-radius: 2px; }}
QFrame[role="step"][tone="active"] {{ background: {_C["foreground"]}; }}

/* ---------------------------------------------------------------- text */
QLabel[role="muted"] {{ color: {_C["muted"]}; }}
QLabel[role="subtle"] {{ color: {_C["subtle"]}; }}
QLabel[role="eyebrow"] {{ color: {_C["subtle"]}; font-weight: 600; }}
QLabel[role="danger"] {{ color: {_C["error"]}; }}
QLabel[role="caption"] {{ color: {_C["subtle"]}; font-size: 10px; }}

/* Pill badge with a status tone (server status, present counter). */
QLabel[role="badge"] {{
    border-radius: 14px;
    padding: 5px 12px;
    font-weight: 600;
    color: {_C["muted"]};
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.10);
}}
QLabel[role="badge"][tone="ok"] {{
    color: {_C["ok"]}; background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.35);
}}
QLabel[role="badge"][tone="warn"] {{
    color: {_C["warn"]}; background: rgba(245, 158, 11, 0.14);
    border: 1px solid rgba(245, 158, 11, 0.35);
}}
QLabel[role="badge"][tone="error"] {{
    color: {_C["error"]}; background: rgba(239, 68, 68, 0.14);
    border: 1px solid rgba(239, 68, 68, 0.35);
}}

/* Inline alert (Not calibrated, clock not synced). */
QLabel[role="alert"] {{ border-radius: 14px; padding: 10px 14px; font-weight: 600; }}
QLabel[role="alert"][tone="warn"] {{
    color: {_C["warn"]}; background: rgba(245, 158, 11, 0.10);
    border: 1px solid rgba(245, 158, 11, 0.30);
}}
QLabel[role="alert"][tone="error"] {{
    color: {_C["error"]}; background: rgba(239, 68, 68, 0.10);
    border: 1px solid rgba(239, 68, 68, 0.30);
}}

/* Live result card floating over the video. */
QLabel[role="banner"] {{ border-radius: 20px; padding: 14px 18px; color: #ffffff; }}
QLabel[role="banner"][tone="neutral"] {{
    background: rgba(9, 9, 11, 0.72); border: 1px solid rgba(255, 255, 255, 0.12);
}}
QLabel[role="banner"][tone="ok"] {{
    background: {_C["ok_solid"]}; border: 2px solid {_C["ok"]};
}}
QLabel[role="banner"][tone="warn"] {{
    background: {_C["warn_solid"]}; border: 2px solid {_C["warn"]};
}}
QLabel[role="banner"][tone="error"] {{
    background: {_C["error_solid"]}; border: 2px solid {_C["error"]};
}}

QLabel[role="video"] {{ background: #000000; color: {_C["subtle"]}; }}
QLabel[role="video"][tone="rounded"] {{ border-radius: 20px; }}

/* ---------------------------------------------------------------- lists */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{
    background: {_C["card"]};
    border: 1px solid {_C["border"]};
    border-radius: 16px;
    padding: 16px 18px;
    margin: 0 0 8px 0;
    color: {_C["foreground"]};
}}
QListWidget::item:selected {{
    background: {_C["primary"]};
    color: {_C["primary_foreground"]};
    border: 1px solid {_C["primary"]};
}}

/* ---------------------------------------------------------------- inputs */
QLineEdit {{
    background: {_C["card"]};
    border: 1px solid {_C["border"]};
    border-radius: 14px;
    padding: 8px 12px;
}}
QLineEdit[role="pin"] {{ background: transparent; border: none; }}
QTextEdit, QPlainTextEdit, QTextBrowser {{
    background: {_C["card"]};
    border: 1px solid {_C["border"]};
    border-radius: 16px;
    padding: 12px;
}}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: {_C["border_strong"]}; border-radius: 3px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""


def font(px: int, weight: int = QFont.Normal) -> QFont:
    """Pixel-sized font, so text is the same size on any panel DPI."""
    result = QFont("Sans")
    result.setPixelSize(px)
    result.setWeight(weight)
    return result


def _palette() -> QPalette:
    """Dark palette for what the stylesheet does not reach (combo popups, selections)."""
    palette = QPalette()
    for role, color in (
        (QPalette.Window, _C["background"]),
        (QPalette.WindowText, _C["foreground"]),
        (QPalette.Base, _C["card"]),
        (QPalette.AlternateBase, _C["card_pressed"]),
        (QPalette.Text, _C["foreground"]),
        (QPalette.Button, _C["card"]),
        (QPalette.ButtonText, _C["foreground"]),
        (QPalette.Highlight, _C["primary"]),
        (QPalette.HighlightedText, _C["primary_foreground"]),
        (QPalette.ToolTipBase, _C["card"]),
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
