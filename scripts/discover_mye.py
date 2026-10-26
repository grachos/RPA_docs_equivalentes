"""Phase 2 reconnaissance: dump the MyE-invoice "Facturación Individual" form structure.

The USER logs in by hand in the opened browser (credentials + captcha). This
script only waits for the logged-in page, then reads the DOM. It never types
credentials, never clicks Registrar/Confirmar, and dumps no cookies or tokens.

    python scripts/discover_mye.py
Output: debug/mye_discovery/{form.json, page.html, page.png}
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"
LOGIN_TIMEOUT_S = 15 * 60

DUMP_JS = """
() => {
  const label = (el) => {
    if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) return l.innerText.trim(); }
    const p = el.closest('.form-group, .col, [class*=col-], td, div');
    const l = p && p.querySelector('label'); return l ? l.innerText.trim() : '';
  };
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  return [...document.querySelectorAll('input, select, textarea, button, a.btn, [role=button]')].map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type || '', id: el.id, name: el.name,
    cls: (el.className || '').toString().slice(0, 80), label: label(el),
    placeholder: el.placeholder || '', text: (el.innerText || el.value || '').trim().slice(0, 60),
    title: el.title || '', visible: visible(el), disabled: !!el.disabled,
    options: el.tagName === 'SELECT' ? [...el.options].map(o => ({v: o.value, t: o.text.trim()})).slice(0, 60) : undefined,
  }));
}
"""


def dump_frame(page: Page) -> list[dict]:
    """Dump the page and every iframe (MyE loads pages inside entrada.jsp)."""
    out = [{"frame": "main", "url": page.url, "elements": page.evaluate(DUMP_JS)}]
    for fr in page.frames[1:]:
        try:
            out.append({"frame": fr.name or "iframe", "url": fr.url, "elements": fr.evaluate(DUMP_JS)})
        except Exception as exc:  # detached frame
            out.append({"frame": fr.name, "url": fr.url, "error": str(exc)})
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR), headless=False, viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.MYE_LOGIN_URL)
        print(">>> Inicie sesión MANUALMENTE en la ventana de Chromium (usuario, contraseña y captcha).")
        print(">>> Cuando vea el menú principal, el script continúa solo.")

        deadline = time.time() + LOGIN_TIMEOUT_S
        while time.time() < deadline and "entrada.jsp" not in page.url:
            page.wait_for_timeout(1000)
        if "entrada.jsp" not in page.url:
            print("No se detectó inicio de sesión a tiempo.")
            ctx.close()
            return 1

        base = page.url.split("entrada.jsp")[0]
        try:
            # The portal keeps connections open, so "load" may never fire.
            page.goto(base + config.MYE_INVOICE_PAGE, wait_until="domcontentloaded", timeout=90_000)
        except Exception as exc:
            print(f"goto no terminó limpio ({type(exc).__name__}); se vuelca lo que haya cargado.")
        page.wait_for_timeout(8000)
        print("URL actual:", page.url)

        (OUT / "form.json").write_text(
            json.dumps(dump_frame(page), ensure_ascii=False, indent=1), encoding="utf-8")
        (OUT / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(OUT / "page.png"), full_page=True)
        print(f"Listo: {OUT}")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
