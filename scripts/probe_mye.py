"""Probe 1 (read-only): list the "Tipo de Documento" options and what appears after choosing one.

Attaches to the browser started by mye_browser.py. Never clicks Previsualizar/Registrar.
Output: debug/mye_discovery/probe1.json + probe1.png
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from app import config  # noqa: E402
from discover_mye import DUMP_JS  # noqa: E402

OUT = ROOT / "debug" / "mye_discovery"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    result: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        if "facturarManualFaseII" not in page.url:
            base = page.url.split("entrada.jsp")[0]
            page.goto(base + config.MYE_INVOICE_PAGE, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_selector("#numeraciones", state="attached", timeout=60_000)

        # The catalog loads asynchronously: wait for real options.
        try:
            page.wait_for_function(
                "document.querySelectorAll('#numeraciones option').length > 1", timeout=30_000)
        except Exception:
            result["numeraciones_note"] = "no cargaron opciones en 30 s con Tipo de Factura=Venta"
        result["numeraciones"] = page.eval_on_selector_all(
            "#numeraciones option", "os => os.map(o => ({v: o.value, t: o.text.trim()}))")
        result["tpFactura_options"] = page.eval_on_selector_all(
            "#tpFactura option", "os => os.map(o => ({v: o.value, t: o.text.trim()}))")
        result["numeraciones_disabled"] = page.eval_on_selector("#numeraciones", "e => e.disabled")

        # Select the document-support numbering only if exactly one option matches.
        matches = [o for o in result["numeraciones"] if re.search(r"soporte|DE25|\bDS\b", o["t"], re.I)]
        result["matches"] = matches
        if len(matches) == 1:
            page.select_option("#numeraciones", value=matches[0]["v"])
            page.wait_for_timeout(5000)
            result["selected"] = matches[0]
            result["after_select"] = page.evaluate(DUMP_JS)

        page.screenshot(path=str(OUT / "probe1.png"), full_page=True)
        (OUT / "probe1.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "after_select"},
                         ensure_ascii=False, indent=1))
        browser.close()  # detaches; the browser stays open
    return 0


if __name__ == "__main__":
    sys.exit(main())
