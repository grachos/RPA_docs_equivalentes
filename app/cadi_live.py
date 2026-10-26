"""Phase 5 (REAL): create the CADI invoice and put it "En revisión".

Resumable per row from the SQLite state:
    ZIP_DOWNLOADED -> (existing invoice with this DE number? adopt it) else fill form,
                      human approval, "Cargar"                       -> CADI_CREATED
    CADI_CREATED   -> "En revisión" (OC, OPERATIVO, reviewer, obs.)  -> CADI_REVIEWED -> DONE
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

from app import config
from app.cadi import Cadi, CadiError, build_form
from app.models import DocState, DocStatus, Row
from app.pdf_check import pdf_issue_date, pdf_text
from app.service import fmt_money, row_observation
from app.state import StateStore
from app.text_utils import normalize_text

log = logging.getLogger("doc_soporte")

APPROVE_LABEL = "Crear en CADI y pasar a En revisión"


def cadi_confirm_text(row: Row, state: DocState, issue: str, observation: str) -> str:
    return ("Se va a CREAR la factura en CADI y pasarla a «En revisión».\n"
            "Estas acciones no se pueden deshacer desde la app.\n\n"
            f"N° de factura: {state.de_number}\nProveedor: {row.provider_name} (NIT {row.provider_id})\n"
            f"Empresa: {config.CADI_COMPANY}\nTipo: {config.CADI_INVOICE_TYPE}\n"
            f"Orden de compra: {row.oc}\nFecha y vencimiento: {issue}\n"
            f"Valor total: $ {fmt_money(row.total)} (IVA 0)\nSoporte: {Path(state.pdf_path or '').name}\n\n"
            f"En revisión → Centro: {config.CADI_CENTER} · Revisado por: {config.CADI_REVIEWER}\n"
            f"Observaciones: {observation}")


class CadiStage:
    def __init__(self, page_factory: Callable[[], Page], ui, observation: str, auto_confirm: bool,
                 attach_xml: bool = False):
        self._page_factory = page_factory
        self._cadi: Cadi | None = None
        self.ui = ui
        self.observation = observation
        self.auto_confirm = auto_confirm
        self.attach_xml = attach_xml

    @property
    def cadi(self) -> Cadi:
        """Log in to CADI only when a row actually reaches this stage."""
        if self._cadi is None:
            cadi = Cadi(self._page_factory())
            self.ui.log("Abriendo CADI…")
            cadi.ensure_logged_in(self.ui.log)
            self._cadi = cadi
        return self._cadi

    def login(self) -> None:
        """Open CADI and wait for the human login now (used at the start of a batch)."""
        _ = self.cadi

    def run(self, store: StateStore, row: Row, state: DocState) -> DocState | None:
        """Advance the row through CADI. Returns the final state, or None if the user declined."""
        self.row_obs = row_observation(state, config.CADI_OBSERVATION_TEMPLATE, self.observation)
        if state.last_ok_status == DocStatus.ZIP_DOWNLOADED:
            state = self.create(store, row, state)
            if state is None:
                return None
        if state.last_ok_status == DocStatus.CADI_CREATED:
            state = self.review(store, row, state)
        if state.last_ok_status == DocStatus.CADI_REVIEWED:
            state = store.advance(row.oc, row.provider_id, DocStatus.DONE)
        return state

    def _observation(self) -> str:
        return getattr(self, "row_obs", None) or self.observation

    def create(self, store: StateStore, row: Row, state: DocState) -> DocState | None:
        cadi = self.cadi
        self.ui.row_update(row.oc, "Factura CADI…", f"{state.de_number}: buscando factura", "")
        existing = cadi.find_invoices(state.de_number)
        others = [i for i in existing if i.nit != row.provider_id]
        if others:
            raise CadiError(f"En CADI ya existe {state.de_number} con otro NIT "
                            f"({', '.join(i.id + ': ' + i.nit for i in others)}). Revise a mano.")
        if len(existing) > 1:
            raise CadiError(f"En CADI hay {len(existing)} facturas {state.de_number}. Revise a mano.")
        if existing:  # created earlier (by hand or a previous run): adopt it, never duplicate
            inv = existing[0]
            log.info("CADI: adopting existing invoice %s for %s", inv.id, state.de_number)
            self.ui.log(f"OC {row.oc}: la factura {state.de_number} ya existe en CADI (ID {inv.id}); "
                        "se usa la existente.")
            return store.advance(row.oc, row.provider_id, DocStatus.CADI_CREATED, cadi_id=inv.id)

        pdf = Path(state.pdf_path or "")
        if not pdf.exists():
            raise CadiError(f"No se encuentra el PDF {pdf}.")
        issue = pdf_issue_date(pdf_text(pdf.read_bytes()))
        if issue is None:
            raise CadiError(f"No se pudo leer la fecha de emisión en {pdf.name}.")
        xml = Path(state.xml_path) if self.attach_xml and state.xml_path else None
        form = build_form(row, state.de_number, issue, pdf, xml)
        self.ui.row_update(row.oc, "Factura CADI…", f"{state.de_number}: llenando factura", "")
        cadi.fill_create_form(form)
        if not self.auto_confirm:
            self.ui.row_update(row.oc, "Esperando confirmación", "formulario CADI listo", "")
            text = cadi_confirm_text(row, state, f"{issue:%d/%m/%Y}", self._observation())
            if not self.ui.confirm(text, pdf, APPROVE_LABEL):
                self.ui.row_update(row.oc, "CADI pendiente", "creación cancelada por el usuario", "")
                return None
        invoice_id = cadi.submit_create(form)
        log.info("CADI: created invoice %s for %s (OC %s)", invoice_id, state.de_number, row.oc)
        self.ui.row_update(row.oc, "Creada en CADI", f"{state.de_number} · CADI {invoice_id}", "")
        return store.advance(row.oc, row.provider_id, DocStatus.CADI_CREATED, cadi_id=invoice_id)

    def review(self, store: StateStore, row: Row, state: DocState) -> DocState:
        cadi = self.cadi
        details = cadi.open_details(state.cadi_id)
        status = normalize_text(details.get("Estado"))
        if status == "EN REVISION":  # already done (by hand or a previous run): just verify
            cadi.verify_reviewed(state.cadi_id, row.oc, row.total)
        elif status == "ABIERTO":
            self.ui.row_update(row.oc, "En revisión…", f"CADI {state.cadi_id}: pasando a En revisión", "")
            cadi.fill_review(state.cadi_id, row.oc, self._observation())
            cadi.submit_review(state.cadi_id, row.oc, row.total)
        else:
            raise CadiError(f"La factura CADI {state.cadi_id} está en «{details.get('Estado')}»; "
                            "no se puede pasar a En revisión.")
        log.info("CADI: invoice %s in review (OC %s)", state.cadi_id, row.oc)
        return store.advance(row.oc, row.provider_id, DocStatus.CADI_REVIEWED)
