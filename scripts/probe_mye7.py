"""Probe 7 (no irreversible actions): address cascade País -> Departamento -> Ciudad of the acquirer.

    python scripts/probe_mye7.py 22222222
Loads a provider with no address, lists the options (value/text) of the three selects and the
catalog requests the page makes. Never previews nor registers.
Output: debug/mye_discovery/probe7.json
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
OPTS = "s => [...document.querySelectorAll(s + ' option')].map(o => ({v: o.value, t: o.text.trim()}))"
SEL = "s => { const e = document.querySelector(s); return e ? {v: e.value, t: (e.options[e.selectedIndex] || {text: ''}).text.trim()} : null; }"


def main(provider_id: str) -> int:
    result: dict = {"requests": []}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        page = browser.contexts[0].pages[0]
        page.on("request", lambda r: "Stores" in r.url and result["requests"].append(
            {"url": r.url[-160:], "post": (r.post_data or "")[:300]}))
        mye = MyeInvoice(page)
        mye.open_form()
        page.evaluate("$('#identificacionAdquiriente').first().closest('.collapse').collapse('show')")
        field = page.locator("#identificacionAdquiriente:visible").first
        pick_provider_suggestion(page, field, provider_id)
        page.wait_for_timeout(5000)
        result["selected_after_provider"] = {s: page.evaluate(SEL, s) for s in (
            "#tiposDocumentosAdquiriente", "#tiposPersonaAdquiriente", "#paisAdquiriente",
            "#departamentosAdquiriente", "#ciudadesAdquiriente")}
        result["inputs"] = page.evaluate("""() => Object.fromEntries(['identificacionAdquiriente',
            'digitoVerificacionAdquiriente', 'nombresAdquiriente', 'apellidosAdquiriente', 'razonSocialAdquiriente',
            'direccionAdquiriente', 'codPostalAdquiriente', 'emailContactoAdquiriente', 'emailEntregaAdquiriente',
            'telefonoAdquiriente'].map(i => [i, (document.querySelector('#' + i) || {}).value]))""")
        paises = page.evaluate(OPTS, "#paisAdquiriente")
        result["paises_count"] = len(paises)
        result["paises_sample"] = [o for o in paises if "olombia" in o["t"]] + paises[:3]
        result["tipos_doc"] = page.evaluate(OPTS, "#tiposDocumentosAdquiriente")
        result["tipos_persona"] = page.evaluate(OPTS, "#tiposPersonaAdquiriente")

        colombia = [o for o in paises if o["t"].strip().lower() == "colombia"]
        if colombia:
            result["requests"].clear()
            page.evaluate("v => $('#paisAdquiriente').val(v).trigger('change')", colombia[0]["v"])
            page.wait_for_timeout(4000)
            deptos = page.evaluate(OPTS, "#departamentosAdquiriente")
            result["departamentos"] = deptos
            result["requests_pais"] = list(result["requests"])
            bog = [o for o in deptos if o["t"].lower().startswith("bogot")]
            ant = [o for o in deptos if o["t"].lower().startswith("antioq")]
            for name, opt in (("bogota", bog), ("antioquia", ant)):
                if opt:
                    result["requests"].clear()
                    page.evaluate("v => $('#departamentosAdquiriente').val(v).trigger('change')", opt[0]["v"])
                    page.wait_for_timeout(4000)
                    cities = page.evaluate(OPTS, "#ciudadesAdquiriente")
                    result[f"ciudades_{name}_count"] = len(cities)
                    result[f"ciudades_{name}_sample"] = cities[:8] + [c for c in cities if "itag" in c["t"].lower()]
                    result[f"requests_{name}"] = list(result["requests"])
        (OUT / "probe7.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        page.screenshot(path=str(OUT / "probe7.png"), full_page=True)
        browser.close()
    print(json.dumps({k: v for k, v in result.items() if k not in ("departamentos",)}, ensure_ascii=False,
                     indent=1)[:7000])
    print("departamentos:", [(d["v"], d["t"]) for d in result.get("departamentos", [])][:40])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
