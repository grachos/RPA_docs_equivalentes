"""Recon helper for CADI: one Chromium (CDP on localhost) kept open after a human login.

    python scripts/cadi_browser.py
Dumps the login form, fills document/password from .env, then the HUMAN solves the
Cloudflare check, presses the login button and types the email code. Stops after
60 minutes or when the window is closed. Local-only recon tool.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

OUT = ROOT / "debug" / "cadi_discovery"
CDP_PORT = 9223

FIELDS_JS = """
() => [...document.querySelectorAll('input, select, textarea, button')].filter(e => e.offsetWidth)
  .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name,
              placeholder: e.placeholder || '', text: (e.innerText || e.value || '').trim().slice(0, 40)}))
"""


def logged_in(url: str) -> bool:
    return url.rstrip("/").startswith(config.CADI_HOME_URL.rstrip("/")) and "login" not in url


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR), headless=False, viewport={"width": 1400, "height": 900},
            args=[f"--remote-debugging-port={CDP_PORT}", "--remote-debugging-address=127.0.0.1"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CADI_LOGIN_URL, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(3000)
        if not logged_in(page.url):
            (OUT / "login.json").write_text(json.dumps(page.evaluate(FIELDS_JS), ensure_ascii=False, indent=1),
                                            encoding="utf-8")
            try:
                pwd = page.locator("input[type=password]:visible").first
                pwd.wait_for(timeout=15_000)
                doc = page.locator("input[type=text]:visible, input[type=number]:visible, "
                                   "input[type=tel]:visible, input[type=email]:visible").first
                doc.fill(config.get_credential("CADI_DOCUMENT"))
                pwd.fill(config.get_credential("CADI_PASSWORD"))
                print(">>> Documento y contraseña llenados. Resuelva la verificación de Cloudflare, "
                      "pulse Ingresar y escriba el código que llega al correo.", flush=True)
            except Exception as exc:
                print(f">>> No se pudo autollenar ({type(exc).__name__}); inicie sesión a mano.", flush=True)
        deadline = time.time() + 60 * 60
        announced = False
        while time.time() < deadline and ctx.pages:
            if not announced and logged_in(page.url):
                print(">>> Sesión de CADI iniciada; deje esta ventana abierta.", flush=True)
                announced = True
            time.sleep(2)
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
