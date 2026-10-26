"""Probe 2 (no irreversible actions): pick "Documento Soporte - DE25 - 8759", then search a provider.

    python scripts/probe_mye2.py 33333333
Selecting the numbering and the provider suggestion only fills the form; it never
clicks Previsualizar/Registrar. Output: debug/mye_discovery/probe2*.json/png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from discover_mye import DUMP_JS  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"
NUMBERING = "8759"

FIELDS_JS = """
() => [...document.querySelectorAll('input, select, textarea')]
  .filter(e => (e.offsetWidth || e.offsetHeight) && e.type !== 'hidden')
  .map(e => ({id: e.id, name: e.name, type: e.type, tag: e.tagName.toLowerCase(),
              value: e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {}).text : e.value,
              readonly: e.readOnly, disabled: e.disabled,
              label: (() => { const l = e.id && document.querySelector(`label[for="${e.id}"]`);
                              if (l) return l.innerText.trim();
                              const g = e.closest('.form-group, [class*=col-]');
                              const gl = g && g.querySelector('label'); return gl ? gl.innerText.trim() : ''; })()}))
"""


def main(provider_id: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]

        if page.eval_on_selector("#numeraciones", "e => e.value") != NUMBERING:
            page.evaluate(f"$('#numeraciones').val('{NUMBERING}').trigger('change')")
            page.wait_for_timeout(6000)

        # Acquirer panel is a collapsed accordion: expand it if the field is hidden.
        field = page.locator("#identificacionAdquiriente:visible").first
        if not field.count():
            page.evaluate("$('#identificacionAdquiriente').first().closest('.collapse').collapse('show')")
            page.wait_for_timeout(1500)
            field = page.locator("#identificacionAdquiriente:visible").first
        print("identification visible:", field.count() == 1)
        (OUT / "probe2_acquirer_open.json").write_text(
            json.dumps(page.evaluate(FIELDS_JS), ensure_ascii=False, indent=1), encoding="utf-8")
        if not field.count():
            page.screenshot(path=str(OUT / "probe2_no_field.png"), full_page=True)
            browser.close()
            return 1

        field.click()
        field.fill("")
        field.press_sequentially(provider_id, delay=120)
        page.wait_for_timeout(4000)
        suggestions = page.evaluate("""() => [...document.querySelectorAll(
            '.ui-autocomplete li, .ui-menu-item, .tt-suggestion, .autocomplete-suggestion, .dropdown-menu.show li, ul.typeahead li')]
            .filter(e => e.offsetWidth).map(e => ({text: e.innerText.trim(), cls: e.className}))""")
        print("suggestions:", json.dumps(suggestions, ensure_ascii=False))
        page.screenshot(path=str(OUT / "probe2_suggestions.png"), full_page=True)

        target = [s for s in suggestions if provider_id in s["text"]]
        if len(target) == 1:
            page.get_by_text(target[0]["text"], exact=True).first.click()
            page.wait_for_timeout(6000)
            (OUT / "probe2_after_provider.json").write_text(
                json.dumps(page.evaluate(FIELDS_JS), ensure_ascii=False, indent=1), encoding="utf-8")
            page.screenshot(path=str(OUT / "probe2_after_provider.png"), full_page=True)
            print("provider selected:", target[0]["text"])
        (OUT / "probe2_full.json").write_text(
            json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
