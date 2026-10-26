"""Phase 4 (simulation): fill the CADI invoice for rows whose MyE ZIP is downloaded.

For each row in ZIP_DOWNLOADED: check CADI has no invoice with the same number,
fill Contabilidad > Facturas > Crear with the final PDF attached, verify every
field and save a screenshot. It never presses "Cargar".
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from playwright.sync_api import Page

from app.cadi import Cadi, CadiError, build_form
from app.models import DocState, Row
from app.pdf_check import pdf_issue_date, pdf_text

log = logging.getLogger("doc_soporte")


def cadi_simulation(page: Page, items: list[tuple[Row, DocState]], ui, cancel: threading.Event,
                    attach_xml: bool = False) -> tuple[int, int]:
    """Returns (ok, failed)."""
    if not items:
        return 0, 0
    cadi = Cadi(page)
    ui.log("Abriendo CADI…")
    cadi.ensure_logged_in(ui.log)
    ok = failed = 0
    for row, state in items:
        if cancel.is_set():
            ui.log("Proceso detenido por el usuario.")
            break
        ui.row_update(row.oc, "Formulario CADI…", f"{state.de_number}: llenando factura", "")
        try:
            detail = _simulate_one(cadi, row, state, attach_xml)
        except CadiError as exc:
            failed += 1
            log.warning("cadi sim oc=%s failed: %s", row.oc, exc)
            ui.row_update(row.oc, "Error CADI", str(exc), "invalid")
            continue
        ok += 1
        log.info("cadi sim oc=%s ok: %s", row.oc, detail)
        ui.row_update(row.oc, "CADI simulación OK", detail, "done")
        ui.log(f"OC {row.oc}: formulario de CADI listo sin cargar ({detail}).")
    return ok, failed


def _simulate_one(cadi: Cadi, row: Row, state: DocState, attach_xml: bool) -> str:
    if not state.de_number or not state.pdf_path:
        raise CadiError("Falta el número DE o el PDF del documento soporte.")
    pdf = Path(state.pdf_path)
    if not pdf.exists():
        raise CadiError(f"No se encuentra el PDF {pdf}.")
    issue = pdf_issue_date(pdf_text(pdf.read_bytes()))
    if issue is None:
        raise CadiError(f"No se pudo leer la fecha de emisión en {pdf.name}.")
    existing = cadi.find_invoices(state.de_number)
    if existing:
        ids = ", ".join(f"{i.id} ({i.status})" for i in existing)
        raise CadiError(f"Ya existe en CADI la factura {state.de_number}: {ids}. No se crea otra.")
    xml = Path(state.xml_path) if attach_xml and state.xml_path else None
    form = build_form(row, state.de_number, issue, pdf, xml)
    try:
        cadi.fill_create_form(form)
    except CadiError:
        raise
    except Exception as exc:
        cadi.snapshot(f"cadi_{row.oc}")
        raise CadiError(f"No se pudo llenar el formulario de CADI: {exc}") from exc
    shot = cadi.screenshot_form(pdf.parent / f"cadi_formulario_{state.de_number}.png")
    return f"{state.de_number} · {issue:%d/%m/%Y} · listo sin «Cargar» · captura {shot.name}"
