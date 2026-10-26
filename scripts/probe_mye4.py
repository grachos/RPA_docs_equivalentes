"""Probe 4 (read-only): structure of "Consultar Documentos Electrónicos".

Dumps filters, toolbar icons and the results table. Clicks nothing that changes data.
Output: debug/mye_discovery/probe4.json + probe4.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"

DUMP_JS = """
() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const lab = e => { const l = e.id && document.querySelector(`label[for="${e.id}"]`);
    if (l) return l.innerText.trim(); const g = e.closest('.form-group, [class*=col-]');
    const gl = g && g.querySelector('label'); return gl ? gl.innerText.trim() : ''; };
  return {
    fields: [...document.querySelectorAll('input, select, textarea')].filter(e => vis(e) && e.type !== 'hidden')
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name, label: lab(e),
                  value: e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {}).text : e.value,
                  options: e.tagName === 'SELECT' ? [...e.options].map(o => o.text.trim()).slice(0, 30) : undefined})),
    buttons: [...document.querySelectorAll('button, a.btn, img[data-tippy-content], i[data-tippy-content], [title], [data-tippy-content]')]
      .filter(vis).map(e => ({tag: e.tagName, id: e.id, cls: e.className.toString().slice(0, 70),
        tip: e.getAttribute('data-tippy-content') || e.title || '', text: (e.innerText || '').trim().slice(0, 30),
        onclick: (e.getAttribute('onclick') || '').slice(0, 80)})).slice(0, 80),
    tables: [...document.querySelectorAll('table')].filter(vis).map(t => ({id: t.id,
      headers: [...t.querySelectorAll('thead th')].map(th => th.innerText.trim()),
      rows: [...t.querySelectorAll('tbody tr')].slice(0, 5).map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim().slice(0, 40)))})),
  };
}
"""


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        base = page.url.split("interfaces/")[0] + "interfaces/"
        page.goto(base + config.MYE_DOCS_PAGE, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(8000)
        data = page.evaluate(DUMP_JS)
        data["url"] = page.url
        (OUT / "probe4.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        page.screenshot(path=str(OUT / "probe4.png"), full_page=True)
        browser.close()
    print(json.dumps(data, ensure_ascii=False)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
