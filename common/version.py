"""Single source of truth for the project version.

``pyproject.toml`` reads this (setuptools dynamic version), the device sends it in
every heartbeat and the admin UI shows it, so a device running stale code is
visible from the dashboard instead of being discovered during a demo.
"""

__version__ = "0.1.0"
