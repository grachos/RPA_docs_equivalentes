"""Single app instance and cleanup of the app's own orphaned browser (Windows).

Only one app window may use the browser profile at a time. If a previous window
was closed without closing its Chromium, that browser keeps the profile locked;
when this is the only app instance, such a browser is an orphan and is closed.
"""
from __future__ import annotations

import logging
import msvcrt
import subprocess
from pathlib import Path

from app import config

log = logging.getLogger("doc_soporte")

LOCK_PATH = config.BASE_DIR / ".app.lock"
_lock_handle = None


def acquire_single_instance() -> bool:
    """True if this is the only running app instance (keeps the lock until exit)."""
    global _lock_handle
    try:
        handle = open(LOCK_PATH, "a+b")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    _lock_handle = handle
    return True


def holds_instance_lock() -> bool:
    return _lock_handle is not None


def close_orphan_browsers(profile_dir: Path = config.BROWSER_PROFILE_DIR) -> int:
    """Close Chromium processes started with this app's profile. Returns how many.

    Call only when this app instance holds the lock: then any such browser belongs
    to a window that is already closed.
    """
    if not holds_instance_lock():
        return 0
    marker = str(profile_dir).replace("'", "''")
    script = (
        "$p = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }}; "
        "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; "
        "($p | Measure-Object).Count"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        count = int((out.stdout or "0").strip().splitlines()[-1] or 0)
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError) as exc:
        log.warning("Could not close orphan browsers: %s", exc)
        return 0
    if count:
        log.info("Closed %s orphaned browser process(es) of the app profile", count)
    return count
