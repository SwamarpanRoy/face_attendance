"""Inline Lucide icons: every name a template asks for exists, labels are escaped."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.app.admin.icons import _ICONS, icon

TEMPLATES = Path(__file__).resolve().parents[2] / "server" / "app" / "templates"
ICON_CALL = re.compile(r"""icon\(\s*['"]([a-z0-9-]+)['"]""")


def test_every_icon_used_in_a_template_exists():
    # An unknown name raises KeyError while rendering, i.e. a 500 on that page.
    used = {
        name
        for path in TEMPLATES.rglob("*.html")
        for name in ICON_CALL.findall(path.read_text(encoding="utf-8"))
    }
    assert used, "templates should use icons"
    assert used <= _ICONS.keys(), f"unknown icons: {sorted(used - _ICONS.keys())}"


def test_icon_is_decorative_by_default_and_escapes_labels():
    plain = str(icon("users", 20))
    assert plain.startswith("<svg") and 'width="20"' in plain and 'aria-hidden="true"' in plain
    labelled = str(icon("users", label='<b>"x"</b>'))
    assert 'role="img"' in labelled and "<b>" not in labelled


def test_unknown_icon_fails_loudly():
    with pytest.raises(KeyError):
        icon("no-such-icon")
