"""Probe 9 (read-only): read the DIAN observation of a rejected document in "Consultar Documentos".

    python scripts/probe_mye9.py 1044444444 DE252300
Filters the provider's documents, opens the "(N Observaciones)" link of that DE and prints the
dialog text. Never sends, regenerates, edits or deletes anything.
Output: debug/mye_discovery/probe9_<DE>.json/.png
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.mye_documents import MyeDocuments  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"


def main(provider_id: str, de: str) -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        docs = MyeDocuments(page)
        rows = docs.search(provider_id, date.today() - timedelta(days=5))
        print("rows:", rows)
        row = page.locator("#tablaDocElectronicos tbody tr", has_text=de).first
        result = {"row_text": row.inner_text()}
        link = row.locator("text=/Observaci/").first
        result["link"] = link.inner_text() if link.count() else None
        before = set(page.evaluate("[...document.querySelectorAll('.modal.show, .swal2-popup, .tippy-box')].map(e => e.id)"))
        if link.count():
            link.click()
            page.wait_for_timeout(3000)
        result["dialogs"] = page.evaluate("""() => [...document.querySelectorAll('.modal.show, .swal2-popup, .tippy-box')]
            .map(e => ({id: e.id, text: e.innerText.trim().slice(0, 4000)}))""")
        page.screenshot(path=str(OUT / f"probe9_{de}.png"), full_page=True)
        (OUT / f"probe9_{de}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
