"""Shared pytest configuration.

Two opt-in test tiers are controlled here so that a bare ``pytest`` stays fast and
dependency-free on any laptop (that is what the "Tests: all" VS Code task runs):

* ``integration`` tests need a reachable PostgreSQL (``TEST_DATABASE_URL``) and run
  only with ``--run-integration``.
* ``pi`` tests need the Raspberry Pi hardware and run only with ``--run-pi``.
"""

from __future__ import annotations

import pytest

_TIERS = {
    "integration": "needs --run-integration and a test PostgreSQL (TEST_DATABASE_URL)",
    "pi": "needs --run-pi on the Raspberry Pi hardware",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in flags for the slow / hardware test tiers."""
    for tier, reason in _TIERS.items():
        parser.addoption(
            f"--run-{tier}",
            action="store_true",
            default=False,
            help=f"run tests marked {tier!r} ({reason})",
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip opt-in tiers unless their flag was given."""
    for tier, reason in _TIERS.items():
        if config.getoption(f"--run-{tier}"):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if tier in item.keywords:
                item.add_marker(skip)
