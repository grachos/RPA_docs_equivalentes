"""Probe 8 (no irreversible actions): acquirer identity fields, obligations and a NEW provider.

    python scripts/probe_mye8.py 22222222 44444444
A) existing provider: what changes when Tipo Documento -> CC and Tipo Persona -> natural
   (visible fields, DV, names), and the obligation checkboxes (labels/state).
B) provider NOT in MyE: after typing its ID, which fields are editable/empty, and any
   "save/update acquirer" control in the section.
Never previews nor registers. Output: debug/mye_discovery/probe8.json (+ section HTML).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.mye_acquirer import pick_provider_suggestion  # noqa: E402
from app.myeinvoice import MyeInvoice  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"
STATE = """() => {
  const sec = document.querySelector('#identificacionAdquiriente').closest('.collapse') || document.body;
  const lab = e => { const l = e.id && document.querySelector(`label[for="${e.id}"]`);
    if (l) return l.innerText.trim();
    const g = e.closest('.form-group, .col-md-3, .col-md-4, .col-md-6, .checkbox, label, div');
    return g ? g.innerText.trim().slice(0, 80) : ''; };
  return [...sec.querySelectorAll('input, select, textarea, button, a.btn')].map(e => ({
    id: e.id, tag: e.tagName, type: e.type || '', value: e.type === 'checkbox' ? e.checked : (e.value || '').slice(0, 60),
    text: e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {text: ''}).text : (e.innerText || '').trim().slice(0, 40),
    visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length), disabled: e.disabled,
    readonly: e.readOnly, label: lab(e), cls: (e.className || '').toString().slice(0, 60)}));
}"""


def dump(page, name: str, result: dict) -> None:
    result[name] = page.evaluate(STATE)


def main(existing: str, new: str) -> int:
    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        mye = MyeInvoice(page)

        # A) existing provider
        mye.open_form()
        page.evaluate("$('#identificacionAdquiriente').first().closest('.collapse').collapse('show')")
        field = page.locator("#identificacionAdquiriente:visible").first
        pick_provider_suggestion(page, field, existing)
        page.wait_for_timeout(5000)
        dump(page, "A_loaded", result)
        (OUT / "probe8_section.html").write_text(page.evaluate(
            "document.querySelector('#identificacionAdquiriente').closest('.collapse').outerHTML"), encoding="utf-8")
        page.evaluate("$('#tiposDocumentosAdquiriente').val('13').trigger('change')")
        page.wait_for_timeout(2500)
        dump(page, "A_after_cc", result)
        page.evaluate("$('#tiposPersonaAdquiriente').val('2').trigger('change')")
        page.wait_for_timeout(2500)
        dump(page, "A_after_natural", result)
        page.screenshot(path=str(OUT / "probe8_A.png"), full_page=True)

        # B) provider that does not exist in MyE
        mye.open_form()
        page.evaluate("$('#identificacionAdquiriente').first().closest('.collapse').collapse('show')")
        field = page.locator("#identificacionAdquiriente:visible").first
        field.fill("")
        field.press_sequentially(new, delay=80)
        page.wait_for_timeout(8000)
        result["B_suggestions"] = page.evaluate(
            "[...document.querySelectorAll('ul.typeahead li')].map(l => l.innerText.trim())")
        field.press("Tab")
        page.wait_for_timeout(3000)
        dump(page, "B_after_typing", result)
        page.screenshot(path=str(OUT / "probe8_B.png"), full_page=True)

        (OUT / "probe8.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    for key in ("A_loaded", "A_after_cc", "A_after_natural", "B_after_typing"):
        print("==", key)
        for e in result[key]:
            if e["visible"] or e["type"] == "checkbox":
                print(f"  {e['id'] or '-':34} {e['tag']:6} {e['type']:10} v={e['value']!r:28} t={e['text']!r:24} "
                      f"dis={e['disabled']} lab={e['label'][:40]!r}")
    print("B_suggestions", result["B_suggestions"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
