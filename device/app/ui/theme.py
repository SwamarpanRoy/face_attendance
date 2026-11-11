"""One look for the whole device UI, after shadcn/ui's "neutral" theme.

shadcn/ui is React + Tailwind, which the device cannot run (PyQt5, 2 GB-class budget, no
web stack), so its design tokens (near-black primary, white cards with a hairline border,
8-12 px radii, pill badges, bordered alerts, grey muted text) are translated into a single
Qt stylesheet here. Widgets only declare *what* they are through dynamic properties
(``variant`` on buttons, ``tone`` on badges/alerts/banners, ``role`` on labels and frames)
and this module decides how that looks, so colours live in one place instead of inline
style strings scattered over the screens.

Sizes (fonts, button heights) stay where the screens set them: the layout was measured to
fit a 480x800 panel and the stylesheet must not change it, so it only adds colour, borders,
radii and modest padding.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QApplication, QWidget

# shadcn "neutral" (light). Status colours are not part of the shadcn palette; these are
# the Tailwind shades shadcn examples use (green/amber/red 600-700 on 50-100 tints).
COLORS = {
    "background": "#ffffff",
    "foreground": "#0a0a0a",
    "muted": "#f5f5f5",
    "muted_foreground": "#737373",
    "border": "#e5e5e5",
    "ring": "#a3a3a3",
    "primary": "#171717",
    "primary_pressed": "#404040",
    "primary_foreground": "#fafafa",
    "secondary_pressed": "#e5e5e5",
    # Slightly darker than shadcn's #ef4444 so white text stays readable on a small panel.
    "destructive": "#dc2626",
    "destructive_pressed": "#b91c1c",
    "ok": "#15803d",
    "ok_soft": "#f0fdf4",
    "ok_border": "#bbf7d0",
    "warn": "#b45309",
    "warn_soft": "#fffbeb",
    "warn_border": "#fde68a",
    "error": "#b91c1c",
    "error_soft": "#fef2f2",
    "error_border": "#fecaca",
    "video": "#0a0a0a",
}

_C = COLORS

STYLESHEET = f"""
QWidget {{
    color: {_C["foreground"]};
}}
QMainWindow, QDialog, QStackedWidget > QWidget {{
    background: {_C["background"]};
}}

/* Buttons: shadcn Button variants. Default = outline (white, hairline border). */
QPushButton {{
    background: {_C["background"]};
    color: {_C["foreground"]};
    border: 1px solid {_C["border"]};
    border-radius: 8px;
    padding: 0 12px;
}}
QPushButton:pressed {{ background: {_C["secondary_pressed"]}; }}
QPushButton:disabled {{ color: {_C["ring"]}; border-color: {_C["muted"]}; }}
QPushButton[variant="primary"] {{
    background: {_C["primary"]};
    color: {_C["primary_foreground"]};
    border: 1px solid {_C["primary"]};
    border-radius: 10px;
}}
QPushButton[variant="primary"]:pressed {{ background: {_C["primary_pressed"]}; }}
QPushButton[variant="destructive"] {{
    background: {_C["destructive"]};
    color: #ffffff;
    border: 1px solid {_C["destructive"]};
    border-radius: 10px;
}}
QPushButton[variant="destructive"]:pressed {{ background: {_C["destructive_pressed"]}; }}
QPushButton[variant="secondary"] {{
    background: {_C["muted"]};
    border: 1px solid {_C["muted"]};
}}
QPushButton[variant="secondary"]:pressed {{ background: {_C["secondary_pressed"]}; }}

/* Card: white, hairline border, rounded-xl. */
QFrame[role="card"] {{
    background: {_C["background"]};
    border: 1px solid {_C["border"]};
    border-radius: 12px;
}}
QFrame[role="card"] QLabel {{ border: none; background: transparent; }}

/* Text roles. */
QLabel[role="muted"] {{ color: {_C["muted_foreground"]}; }}
QLabel[role="caption"] {{ color: {_C["muted_foreground"]}; font-size: 10px; }}
QLabel[role="danger"] {{ color: {_C["error"]}; }}
QLabel[role="code"] {{
    background: {_C["muted"]};
    border: 1px solid {_C["border"]};
    border-radius: 8px;
    padding: 8px;
}}

/* Badge: rounded-full pill. */
QLabel[role="badge"] {{
    border-radius: 11px;
    padding: 3px 12px;
    font-weight: 600;
    border: 1px solid {_C["border"]};
    background: {_C["muted"]};
}}
QLabel[role="badge"][tone="ok"] {{
    color: {_C["ok"]}; background: {_C["ok_soft"]}; border-color: {_C["ok_border"]};
}}
QLabel[role="badge"][tone="warn"] {{
    color: {_C["warn"]}; background: {_C["warn_soft"]}; border-color: {_C["warn_border"]};
}}
QLabel[role="badge"][tone="error"] {{
    color: {_C["error"]}; background: {_C["error_soft"]}; border-color: {_C["error_border"]};
}}

/* Alert: bordered, rounded-lg, padded; empty alerts take no room. */
QLabel[role="alert"] {{
    border-radius: 10px;
    padding: 10px 14px;
    font-weight: 600;
}}
QLabel[role="alert"][tone="warn"] {{
    color: {_C["warn"]}; background: {_C["warn_soft"]}; border: 1px solid {_C["warn_border"]};
}}
QLabel[role="alert"][tone="error"] {{
    color: {_C["error"]}; background: {_C["error_soft"]}; border: 1px solid {_C["error_border"]};
}}

/* Live banner: the one big status line, solid colours so it reads from a distance. */
QLabel[role="banner"] {{ border-radius: 14px; padding: 12px; color: #ffffff; }}
QLabel[role="banner"][tone="neutral"] {{ background: {_C["primary"]}; }}
QLabel[role="banner"][tone="ok"] {{ background: {_C["ok"]}; }}
QLabel[role="banner"][tone="warn"] {{ background: {_C["warn"]}; }}
QLabel[role="banner"][tone="error"] {{ background: {_C["error"]}; }}

QLabel[role="video"] {{
    background: {_C["video"]};
    color: {_C["muted_foreground"]};
    border-radius: 12px;
}}

/* Lists (course, section, period, roster): bordered card, roomy rows. */
QListWidget {{
    background: {_C["background"]};
    border: 1px solid {_C["border"]};
    border-radius: 12px;
    padding: 4px;
    outline: 0;
}}
QListWidget::item {{
    padding: 14px;
    border-radius: 8px;
    margin: 2px 0;
}}
QListWidget::item:selected {{
    background: {_C["primary"]};
    color: {_C["primary_foreground"]};
}}

/* Inputs: PIN display, USN filter. (QComboBox keeps Fusion's own frame: a stylesheet
   border on it makes Qt drop the native drop-down arrow.) */
QLineEdit {{
    background: {_C["background"]};
    border: 1px solid {_C["border"]};
    border-radius: 8px;
    padding: 6px 10px;
}}
QLineEdit:focus {{ border: 1px solid {_C["ring"]}; }}

QTextBrowser, QTextEdit, QPlainTextEdit {{
    background: {_C["muted"]};
    border: 1px solid {_C["border"]};
    border-radius: 10px;
    padding: 8px;
}}
"""


def apply(app: QApplication) -> None:
    """Install the theme for every window and dialog the app creates."""
    app.setStyle("Fusion")  # same rendering on the Pi and the laptop simulator
    app.setStyleSheet(STYLESHEET)


def set_props(widget: QWidget, **props: str) -> None:
    """Set dynamic properties and re-apply the stylesheet (Qt does not on its own)."""
    for name, value in props.items():
        widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
