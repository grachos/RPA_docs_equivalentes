"""Probe 5 (read-only): search an existing document, inspect the results, download its ZIP.

    python scripts/probe_mye5.py 11111111 2026-10-01
Downloading the AttachedDocument ZIP does not change anything in MyE.
Output: debug/mye_discovery/probe5*.json/png and debug/mye_discovery/zip/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from app import config  # noqa: E402
from probe_mye4 import DUMP_JS  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"


def main(provider_id: str, date_from: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        if "administrarDocElectronicos" not in page.url:
            base = page.url.split("interfaces/")[0] + "interfaces/"
            page.goto(base + config.MYE_DOCS_PAGE, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(6000)
        page.evaluate("""([d]) => { for (const id of ['fechaInicialReg']) {
            const e = document.querySelector('#' + id); e.value = d; $(e).trigger('change'); } }""", [date_from])
        page.evaluate("$('#cmbNume').val($('#cmbNume option').filter((i, o) => o.text.trim() === '8759 - DE25').val()).trigger('change')")
        ident = page.locator("#identificacion:visible").first
        ident.fill("")
        ident.fill(provider_id)
        page.locator("#btnSearch").click()
        # Wait until DataTables finishes ("Procesando" indicator hidden), up to 90 s.
        page.wait_for_timeout(1500)
        for _ in range(90):
            busy = page.evaluate("""() => [...document.querySelectorAll('.dataTables_processing')]
                .some(e => e.offsetWidth && getComputedStyle(e).display !== 'none')""")
            if not busy:
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(1000)
        after = page.evaluate(DUMP_JS)
        (OUT / "probe5_results.json").write_text(json.dumps(after, ensure_ascii=False, indent=1), encoding="utf-8")
        page.screenshot(path=str(OUT / "probe5_results.png"), full_page=True)

        # Select the first result row (checkbox or row click) and list the toolbar icons.
        first = page.locator("table tbody tr").filter(has_text=provider_id).first
        result = {"rows_with_id": page.locator("table tbody tr").filter(has_text=provider_id).count()}
        if result["rows_with_id"]:
            cb = first.locator("input[type=checkbox]")
            (cb.first if cb.count() else first).click()
            page.wait_for_timeout(1500)
            result["toolbar"] = page.evaluate("""() => [...document.querySelectorAll(
                '[data-tippy-content], [title], .dt-buttons button, .toolbar-container *')]
                .filter(e => e.offsetWidth).map(e => ({tag: e.tagName, id: e.id,
                  tip: e.getAttribute('data-tippy-content') || e.title || '',
                  cls: e.className.toString().slice(0, 60), disabled: !!e.disabled}))""")
            page.screenshot(path=str(OUT / "probe5_selected.png"), full_page=True)
            zip_btn = page.locator("[data-tippy-content*='AttachedDocument' i]:visible, "
                                   "[title*='AttachedDocument' i]:visible").first
            if zip_btn.count():
                (OUT / "zip").mkdir(parents=True, exist_ok=True)
                with page.expect_download(timeout=60_000) as dl:
                    zip_btn.click()
                target = OUT / "zip" / (dl.value.suggested_filename or "attached.zip")
                dl.value.save_as(target)
                result["zip"] = str(target)
        (OUT / "probe5.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
