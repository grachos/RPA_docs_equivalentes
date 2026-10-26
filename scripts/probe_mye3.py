"""Probe 3 (read-only): Conceptos / Adjuntos modals, graphic representation and payment options.

Opens each "+" modal, dumps its fields, and closes it WITHOUT clicking "Agregar".
Output: debug/mye_discovery/probe3.json + screenshots
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "debug" / "mye_discovery"

OPTIONS_JS = "sel => [...document.querySelectorAll(sel + ' option')].map(o => ({v: o.value, t: o.text.trim()}))"

SECTION_JS = """
(title) => {
  const hdr = [...document.querySelectorAll('.card-header, [data-toggle=collapse]')]
    .find(h => h.innerText.trim().startsWith(title));
  if (!hdr) return null;
  const card = hdr.closest('.card') || hdr.parentElement;
  const target = hdr.getAttribute('data-target') || (hdr.querySelector('[data-target]') || {}).getAttribute?.('data-target');
  return {target, header_html: hdr.outerHTML.slice(0, 300),
          buttons: [...card.querySelectorAll('button, a, i.fa-plus, span')].filter(b =>
             /plus|agregar|add|\\+/i.test(b.className + ' ' + (b.title || '') + ' ' + (b.getAttribute('onclick') || '') + ' ' + b.innerText))
             .slice(0, 15).map(b => ({tag: b.tagName, id: b.id, cls: b.className.toString().slice(0, 60),
                                      title: b.title, onclick: (b.getAttribute('onclick') || '').slice(0, 80),
                                      text: b.innerText.trim().slice(0, 30)}))};
}
"""

MODAL_JS = """
() => { const m = [...document.querySelectorAll('.modal')].find(x => x.classList.contains('show') || x.style.display === 'block');
  if (!m) return null;
  return {id: m.id, title: (m.querySelector('.modal-title') || {innerText: ''}).innerText.trim(),
    fields: [...m.querySelectorAll('input, select, textarea, button')].filter(e => e.offsetWidth || e.tagName === 'SELECT')
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name,
        label: (() => { const g = e.closest('.form-group, [class*=col-]'); const l = g && g.querySelector('label');
                        return l ? l.innerText.trim() : ''; })(),
        text: (e.innerText || e.value || '').trim().slice(0, 40), onclick: (e.getAttribute('onclick') || '').slice(0, 60),
        options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()).slice(0, 40) : undefined}))}; }
"""


def open_section(page: Page, title: str) -> dict | None:
    info = page.evaluate(SECTION_JS, title)
    if info and info.get("target"):
        page.evaluate(f"$('{info['target']}').collapse('show')")
        page.wait_for_timeout(1200)
        info = page.evaluate(SECTION_JS, title)
    return info


def probe_modal(page: Page, title: str, name: str, result: dict) -> None:
    info = open_section(page, title)
    result[name] = {"section": info}
    # The "+" is <img class="add_img_agregar_icons" data-tippy-content="Agregar ..."> in the table header.
    plus = page.locator(f"{info['target']} img.add_img_agregar_icons:visible").first \
        if info and info.get("target") else None
    if plus is not None:
        result[name]["plus_title"] = plus.get_attribute("data-tippy-content") if plus.count() else None
    if plus is None or not plus.count():
        result[name]["error"] = "no se encontró botón +"
        return
    plus.click()
    page.wait_for_timeout(2500)
    result[name]["modal"] = page.evaluate(MODAL_JS)
    page.screenshot(path=str(OUT / f"probe3_{name}.png"))
    page.keyboard.press("Escape")  # close without "Agregar"
    page.wait_for_timeout(800)
    page.evaluate("$('.modal.show').modal('hide')")
    page.wait_for_timeout(800)


def main() -> int:
    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        result["reportes"] = page.evaluate(OPTIONS_JS, "#reportes")
        result["metodosPago"] = page.evaluate(OPTIONS_JS, "#metodosPago")
        result["mediosPago"] = page.evaluate(OPTIONS_JS, "#mediosPago")
        result["tpAdjuntoAdd"] = page.evaluate(OPTIONS_JS, "#tpAdjuntoAdd")
        probe_modal(page, "Conceptos", "conceptos", result)
        probe_modal(page, "Adjuntos", "adjuntos", result)
        # Payment section fields appearing for "Crédito" (only reading; select is reversible).
        credit = [o for o in result["metodosPago"] if o["t"].lower().startswith("cr")]
        if len(credit) == 1:
            page.evaluate(f"$('#metodosPago').val('{credit[0]['v']}').trigger('change')")
            page.wait_for_timeout(2500)
            result["after_credit"] = page.evaluate("""() => [...document.querySelectorAll(
                '#metodosPago, #mediosPago, #identificadorPago, input[id*=encimiento], input[id*=Vencimiento]')]
                .map(e => ({id: e.id, visible: !!e.offsetWidth, value: e.value}))""")
            result["mediosPago_credit"] = page.evaluate(OPTIONS_JS, "#mediosPago")
        page.screenshot(path=str(OUT / "probe3_payment.png"), full_page=True)
        (OUT / "probe3.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=1)[:12000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
