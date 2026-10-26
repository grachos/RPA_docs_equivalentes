"""Probe 6: fill a row, click Previsualizar and dump the preview dialog's buttons.

NEVER clicks Confirmar/Registrar: the dialog is closed with its dismiss control.
    python scripts/probe_mye6.py <provider_id> "<name>" <oc> <total> "<observation>"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.models import Row  # noqa: E402
from app.myeinvoice import MyeInvoice  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"

MODALS_JS = """
() => [...document.querySelectorAll('.modal')].filter(m => m.classList.contains('show') || m.style.display === 'block')
  .map(m => ({id: m.id, title: (m.querySelector('.modal-title') || {innerText: ''}).innerText.trim(),
    body: (m.querySelector('.modal-body') || {innerText: ''}).innerText.trim().slice(0, 300),
    buttons: [...m.querySelectorAll('button, a.btn, input[type=button], input[type=submit]')]
      .filter(b => b.offsetWidth).map(b => ({id: b.id, text: (b.innerText || b.value || '').trim(),
        cls: b.className.toString().slice(0, 60), dismiss: b.getAttribute('data-dismiss'),
        onclick: (b.getAttribute('onclick') || '').slice(0, 80)}))}))
"""


def main(pid: str, name: str, oc: str, total: str, obs: str) -> int:
    row = Row(excel_row=2, provider_id=pid, provider_name=name, oc=oc, total=int(total))
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        mye = MyeInvoice(page)
        mye.open_form()
        mye.fill_acquirer(row)
        mye.add_concept(row.total)
        mye.add_po_attachment(row.oc)
        mye.set_footer(row, obs)
        mye.check_totals(row.total)
        mye.fix_names()
        mye.assert_acquirer(row)
        with page.expect_response(lambda r: "/Facturar" in r.url and r.request.method == "POST",
                                  timeout=90_000) as info:
            page.locator("#previsualizarFactura").click()
        data = info.value.json()
        page.wait_for_timeout(4000)
        modals = page.evaluate(MODALS_JS)
        page.screenshot(path=str(OUT / "probe6_preview_dialog.png"))
        req = info.value.request.post_data or ""
        result = {"success": data.get("success"), "msg": data.get("msg"), "has_pdf": bool(data.get("pdf")),
                  "request_accion": [p for p in req.split("&") if p.startswith(("accion=", "idnumeracion="))],
                  "modals": modals}
        (OUT / "probe6.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        mye._close_dialogs()  # dismiss only; never confirm
        page.wait_for_timeout(1000)
        print("modals after close:", page.evaluate(MODALS_JS))
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:6]))
