"""CADI probe (read-only): invoices list and the "create" form structure.

Attaches to scripts/cadi_browser.py (CDP 9223). Only navigates and reads; never
submits a form. Output: debug/cadi_discovery/{list,create}.json + .png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402

OUT = ROOT / "debug" / "cadi_discovery"

DUMP_JS = """
() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.type === 'file');
  const lab = e => { const l = e.id && document.querySelector(`label[for="${e.id}"]`);
    if (l) return l.innerText.trim(); const g = e.closest('.form-group, .mb-3, [class*=col-], td, div');
    const gl = g && g.querySelector('label'); return gl ? gl.innerText.trim() : ''; };
  return {
    url: location.href, title: document.title,
    forms: [...document.querySelectorAll('form')].map(f => ({id: f.id, action: f.action, method: f.method, enctype: f.enctype})),
    fields: [...document.querySelectorAll('input, select, textarea')].filter(e => vis(e) && e.type !== 'hidden')
      .map(e => ({tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name, label: lab(e),
                  placeholder: e.placeholder || '', required: e.required, maxlength: e.maxLength,
                  value: e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {}).text : (e.type === 'file' ? '' : e.value),
                  options: e.tagName === 'SELECT' ? [...e.options].map(o => o.value + ' | ' + o.text.trim()).slice(0, 40) : undefined})),
    hidden: [...document.querySelectorAll('input[type=hidden]')].map(e => e.name).slice(0, 40),
    buttons: [...document.querySelectorAll('button, a.btn, input[type=submit], input[type=button]')].filter(vis)
      .map(b => ({tag: b.tagName, id: b.id, type: b.type, text: (b.innerText || b.value || '').trim().slice(0, 40),
                  href: b.href || '', cls: b.className.toString().slice(0, 60)})).slice(0, 60),
    tables: [...document.querySelectorAll('table')].filter(vis).map(t => ({id: t.id,
      headers: [...t.querySelectorAll('thead th')].map(th => th.innerText.trim()),
      rows: [...t.querySelectorAll('tbody tr')].slice(0, 4).map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim().slice(0, 30)))})),
    links: [...document.querySelectorAll('a')].filter(vis).map(a => (a.innerText.trim().slice(0, 30) + ' -> ' + a.getAttribute('href'))).filter(t => /invoice|factur/i.test(t)).slice(0, 30),
  };
}
"""


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = browser.contexts[0].pages[0]
        print("current:", page.url)
        for name, url in (("list", config.CADI_HOME_URL + "accounting/invoices"),
                          ("create", config.CADI_CREATE_URL)):
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(4000)
            data = page.evaluate(DUMP_JS)
            (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            print(name, "->", data["url"], "|", data["title"])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
