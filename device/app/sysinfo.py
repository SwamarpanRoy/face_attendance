"""Small facts about the host for the idle screen and heartbeats.

Linux (Raspberry Pi OS) paths are the primary implementation; every function degrades
to ``None``/``True`` on the laptop so the simulator never crashes on a missing file.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import socket
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
MEMINFO = Path("/proc/meminfo")


def _run(cmd: list[str], timeout: float = 2.0) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def ip_address() -> str | None:
    """The address the default route would use; no packets are actually sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            return str(sock.getsockname()[0])
    except OSError:
        return None


def ssid() -> str | None:
    if sys.platform.startswith("linux"):
        out = _run(["iwgetid", "-r"])
        if out:
            return out
        out = _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"])
        if out:
            for line in out.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1] or None
        return None
    if sys.platform == "win32":
        out = _run(["netsh", "wlan", "show", "interfaces"])
        if out:
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID") and not stripped.startswith("SSID BSSID"):
                    return stripped.split(":", 1)[1].strip() or None
    return None


def cpu_temp_c() -> float | None:
    with contextlib.suppress(OSError, ValueError):
        return int(THERMAL.read_text().strip()) / 1000.0
    return None


def free_mem_mb() -> int | None:
    with contextlib.suppress(OSError, ValueError):
        for line in MEMINFO.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return int(status.ullAvailPhys // (1024 * 1024))
    return None


def clock_synced() -> bool:
    """The Pi 4 has no RTC: trust the clock only when systemd-timesyncd says NTP synced."""
    if sys.platform.startswith("linux"):
        out = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        if out is not None:
            return out.strip().lower() == "yes"
    return True


def throttled_state() -> str | None:
    """Raspberry Pi firmware throttling flags (``vcgencmd get_throttled``)."""
    return _run(["vcgencmd", "get_throttled"])
