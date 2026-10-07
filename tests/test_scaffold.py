"""Guard rails for the repository scaffold.

Deliberately boring tests that fail loudly if someone breaks package importability
or removes a privacy-critical ignore rule. The second case would otherwise only be
noticed when biometrics, secrets or 100 MB model files turn up in a commit.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "package",
    [
        "common",
        "common.face",
        "device.app",
        "device.app.ui",
        "server.app",
        "server.app.api",
        "server.app.admin",
        "server.app.services",
    ],
)
def test_packages_import_without_hardware_or_gui_deps(package: str) -> None:
    importlib.import_module(package)


@pytest.mark.parametrize(
    "pattern",
    [".env", "device.toml", "models/*", "data/", "backups/", "*.onnx", "*.db", ".venv/"],
)
def test_gitignore_protects_secrets_models_and_biometrics(pattern: str) -> None:
    lines = {ln.strip() for ln in (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()}
    assert pattern in lines, f"{pattern} must stay in .gitignore"


def test_env_example_documents_required_keys_with_placeholders() -> None:
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    for key in ("DATABASE_URL", "SECRET_KEY", "CROPS_DIR", "FACULTY_EDIT_WINDOW_DAYS"):
        assert f"{key}=" in text, f"{key} missing from .env.example"
    assert "change-me" in text, "placeholders, never real secrets, in .env.example"
