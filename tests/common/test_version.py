"""The version string is read by packaging, heartbeats and the admin UI."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from common.version import __version__

REPO = Path(__file__).resolve().parents[2]


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?", __version__)


def test_pyproject_reads_version_from_common() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in data["project"]["dynamic"]
    attr = data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "common.version.__version__"
