"""Recon helper: keep ONE Chromium open (CDP on localhost) so probes can reuse your login.

    python scripts/mye_browser.py     # log in by hand (credentials + captcha), leave it running
    python scripts/probe_mye.py       # attaches to this browser and inspects the form

Stops after 60 minutes or when the window is closed. Local-only recon tool.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

CDP_PORT = 9222


def main() -> int:
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR), headless=False, viewport={"width": 1400, "height": 900},
            args=[f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        from app.myeinvoice import MyeInvoice  # fills user/password from .env

        MyeInvoice(page).ensure_logged_in(lambda m: print(">>>", m, flush=True))
        print(">>> Sesión iniciada; deje esta ventana abierta.", flush=True)
        deadline = time.time() + 60 * 60
        while time.time() < deadline and ctx.pages:
            time.sleep(2)
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
