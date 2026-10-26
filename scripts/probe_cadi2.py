"""CADI probe 2 (read-only): details page of an "Abierto" invoice and the "En revisión" modal.

    python scripts/probe_cadi2.py <invoice_id>
Opens the modal to read its fields and closes it WITHOUT pressing its submit button.
Output: debug/cadi_discovery/details_<id>.json + .png, review_modal_<id>.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "debug" / "cadi_discovery"

BUTTONS_JS = """
() => [...document.querySelectorAll('button, a, input[type=submit], input[type=button]')]
  .filter(e => e.offsetWidth && (e.innerText || e.value || '').trim())
  .map(e => ({tag: e.tagName, id: e.id, text: (e.innerText || e.value || '').trim().slice(0, 40),
              cls: e.className.toString().slice(0, 70), href: e.getAttribute('href') || '',
              target: e.getAttribute('data-target') || e.getAttribute('data-bs-target') || '',
              onclick: (e.getAttribute('onclick') || '').slice(0, 80)}))
"""

MODAL_JS = """
() => [...document.querySelectorAll('.modal')].filter(m => m.classList.contains('show') || getComputedStyle(m).display === 'block')
  .map(m => ({id: m.id, title: (m.querySelector('.modal-title, h4, h5') || {innerText: ''}).innerText.trim(),
    form: (() => { const f = m.querySelector('form'); return f ? {action: f.action, method: f.method} : null; })(),
    fields: [...m.querySelectorAll('input, select, textarea')].filter(e => e.type !== 'hidden')
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name, required: e.required,
        label: (() => { const l = e.id && m.querySelector(`label[for="${e.id}"]`); if (l) return l.innerText.trim();
                        const g = e.closest('.form-group, .mb-3, [class*=col-]'); const gl = g && g.querySelector('label');
                        return gl ? gl.innerText.trim() : ''; })(),
        value: e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {text: ''}).text : e.value,
        options: e.tagName === 'SELECT' ? [...e.options].map(o => o.value + ' | ' + o.text.trim()).slice(0, 25) : undefined})),
    hidden: [...m.querySelectorAll('input[type=hidden]')].map(e => e.name + '=' + e.value),
    buttons: [...m.querySelectorAll('button, input[type=submit]')].map(b => ({id: b.id, type: b.type,
      text: (b.innerText || b.value || '').trim(), dismiss: b.getAttribute('data-dismiss') || b.getAttribute('data-bs-dismiss')}))}))
"""


def main(invoice_id: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = browser.contexts[0].pages[0]
        page.goto(f"https://cadi.com.co/cadi/accounting/invoices/details?i={invoice_id}",
                  wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(3000)
        result = {"url": page.url, "buttons": page.evaluate(BUTTONS_JS)}
        page.screenshot(path=str(OUT / f"details_{invoice_id}.png"), full_page=True)
        review = page.locator("a:visible, button:visible").filter(has_text="En revisión")
        result["review_buttons"] = review.count()
        if review.count() == 1:
            review.first.click()
            page.wait_for_timeout(2500)
            result["modal"] = page.evaluate(MODAL_JS)
            page.screenshot(path=str(OUT / f"review_modal_{invoice_id}.png"))
            page.keyboard.press("Escape")  # close WITHOUT submitting
            page.wait_for_timeout(800)
            page.evaluate("document.querySelectorAll('.modal.show [data-dismiss=modal], .modal.show [data-bs-dismiss=modal]')"
                          ".forEach(b => b.click())")
            page.wait_for_timeout(800)
            result["modal_after_close"] = page.evaluate(MODAL_JS)
        (OUT / f"details_{invoice_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
        browser.close()
    print(json.dumps({k: v for k, v in result.items() if k != "buttons"}, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
