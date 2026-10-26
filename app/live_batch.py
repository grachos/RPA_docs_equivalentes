"""REAL mode: MyE-invoice + DIAN + ZIP (phase 3), then CADI (phase 5, see cadi_live.py).

Per row, resumable from the SQLite state:
    PENDING        -> duplicate check -> fill + preview -> human approval -> Confirmar
    MYE_REGISTERED -> wait "Procesado Correctamente"
    DIAN_OK        -> download AttachedDocument ZIP, extract PDF/XML, validate
    ZIP_DOWNLOADED -> CADI create (+ approval) -> CADI_CREATED -> En revisión -> DONE
A row is never registered twice: before Confirmar the app looks for an existing
document of the same provider and amount, and an unreadable DE number stops the batch.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

from app import config
from app.cadi import CadiError, CadiUnknown
from app.cadi_live import CadiStage
from app.error_guide import annotate
from app.models import DocState, DocStatus, Row
from app.mye_documents import DocRow, DocumentsError, MyeDocuments, duplicates_in
from app.myeinvoice import MyeError, MyeInvoice, PreviewResult, RegisteredUnknown, SessionExpired
from app.pdf_check import check_pdf_text, pdf_text
from app.service import fmt_money, row_observation
from app.state import StateStore
from app.zip_utils import extract_pdf_and_xml, safe_folder_name

log = logging.getLogger("doc_soporte")


class StopBatch(Exception):
    """Stop the whole batch (unknown state that a human must check)."""


def batch_folder(consolidado: str) -> Path:
    return config.DOWNLOADS_DIR / safe_folder_name("", consolidado).strip("_")


def confirm_text(row: Row, observation: str, preview: PreviewResult) -> str:
    warn = ("\n\nAvisos:\n- " + "\n- ".join(preview.warnings)) if preview.warnings else ""
    return ("Se va a REGISTRAR el documento soporte y se enviará a la DIAN.\n"
            "Esta acción no se puede deshacer.\n\n"
            f"Proveedor: {row.provider_name}\nID: {row.provider_id}\nOC: {row.oc}\n"
            f"Total: $ {fmt_money(row.total)}\nObservación: {observation}\n\n"
            f"Vista previa validada: OK{warn}")


class LiveBatch:
    def __init__(self, page: Page, ui, observation: str, corte_date: date, auto_confirm: bool,
                 cancel: threading.Event, cadi_page: Callable[[], Page] | None = None,
                 cadi_observation: str | None = None, docs_page: Page | None = None):
        self.mye = MyeInvoice(page)
        # The documents query runs in its own tab when given, so it can overlap the form load.
        self.docs = MyeDocuments(docs_page or page)
        self._overlap = docs_page is not None and docs_page is not page
        self.ui = ui
        self.observation = observation
        self.auto_confirm = auto_confirm
        self.cancel = cancel
        self._mye_ready = False
        # OC -> (provider name in MyE, surnames) seen at registration, for the final PDF check.
        self.acquirers: dict[str, tuple[str | None, str | None]] = {}
        # DE number -> OC of the documents created in this run (not yet in CADI).
        self.created_oc: dict[str, str] = {}
        self.cadi = (CadiStage(cadi_page, ui, cadi_observation or observation, auto_confirm)
                     if cadi_page else None)
        # Look back far enough to catch a document created for this corte earlier.
        self.since = min(corte_date, date.today()) - timedelta(days=config.DUPLICATE_LOOKBACK_DAYS)

    def ensure_mye(self) -> None:
        """Log in to MyE only when a row needs it (rows already in CADI do not)."""
        if not self._mye_ready:
            self.ui.log("Abriendo MyE-invoice…")
            self.mye.ensure_logged_in(self.ui.log)
            self._mye_ready = True

    @contextmanager
    def timed(self, step: str, oc: str):
        """Measure a step; durations go to the log and to the end-of-batch summary."""
        start = time.monotonic()
        try:
            yield
        finally:
            secs = time.monotonic() - start
            self.times[step].append(secs)
            self.row_times[oc][step] = secs
            self._pending_times.append((oc, step, secs))
            log.info("TIMING oc=%s step=%s secs=%.1f", oc, step, secs)

    def _flush_times(self, store: StateStore, row: Row) -> None:
        """Persist the measured times of this row (shown in the report)."""
        for oc, step, secs in self._pending_times:
            if oc == row.oc:
                store.record_timing(row.oc, row.provider_id, step, secs)
        self._pending_times = [t for t in self._pending_times if t[0] != row.oc]

    def row_times_text(self) -> str:
        lines = []
        for oc, steps in self.row_times.items():
            parts = " · ".join(f"{step} {secs:.0f} s" for step, secs in steps.items())
            lines.append(f"OC {oc}: {parts} · total {sum(steps.values()) / 60:.1f} min")
        return "\n".join(lines)

    def summary_rows(self) -> list[tuple[str, object]]:
        """Batch totals for the report's "Resumen" sheet."""
        rows: list[tuple[str, object]] = [
            ("Inicio", self.started_at), ("Fin", self.ended_at),
            ("Tiempo total del lote (min)", round(self.total_secs / 60, 2)),
            ("Filas con tiempos medidos", len(self.row_times)),
        ]
        for step, values in self.times.items():
            rows.append((f"{step}: promedio (s)", round(sum(values) / len(values), 1)))
            rows.append((f"{step}: cantidad", len(values)))
        return rows

    def timing_summary(self, total_secs: float) -> str:
        parts = [f"{step}: {sum(v) / len(v):.0f} s prom. ({len(v)})" for step, v in self.times.items() if v]
        return f"Tiempo total: {total_secs / 60:.1f} min. " + (" · ".join(parts) if parts else "")

    def login_upfront(self, store: StateStore, items: list[tuple[Row, DocState]]) -> None:
        """Ask for every human step (captcha, Cloudflare, email code) at the start, so the
        rest of the batch can run unattended."""
        states = [store.get(r.oc, r.provider_id) for r, _ in items]
        steps = {s.last_ok_status for s in states if s}
        if steps & {DocStatus.PENDING, DocStatus.MYE_REGISTERED, DocStatus.DIAN_OK}:
            self.ensure_mye()
        if self.cadi and steps - {DocStatus.DONE}:
            self.cadi.login()
        self.ui.log("Sesiones listas: el lote sigue solo.")

    def prefetch_terceros(self, store: StateStore, items: list[tuple[Row, DocState]]) -> None:
        """Ask Silogtran for the providers of the rows still to register, in the background
        (it overlaps the logins; the first call may wake the service up, ~1 min)."""
        ids = [r.provider_id for r, _ in items
               if (s := store.get(r.oc, r.provider_id)) and s.last_ok_status == DocStatus.PENDING]
        api = getattr(self.mye, "terceros", None)
        if ids and api is not None:
            api.prefetch(ids)

    def load_dupe_index(self, store: StateStore, items: list[tuple[Row, DocState]]) -> None:
        """One duplicate search for the whole batch (instead of one per row)."""
        self.dupe_index = None
        pending = [r for r, _ in items
                   if (s := store.get(r.oc, r.provider_id)) and s.last_ok_status == DocStatus.PENDING]
        if len(pending) < 2:  # not worth it for a single row
            return
        self.ui.log("Buscando documentos existentes en MyE (una vez para todo el lote)…")
        start = time.monotonic()
        try:
            self.dupe_index = self.docs.search_all(self.since)
        except Exception as exc:  # an optimization must never stop the batch: per-row search instead
            log.warning("Batch duplicate search failed, per-row search instead: %s", exc)
            self.dupe_index = None
        self.times["Búsqueda duplicados (lote)"].append(time.monotonic() - start)
        log.info("TIMING step=busqueda_duplicados_lote secs=%.1f docs=%s", time.monotonic() - start,
                 None if self.dupe_index is None else len(self.dupe_index))

    def run(self, items: list[tuple[Row, DocState]], consolidado: str) -> str:
        """Two passes: register every row in MyE first, then (while the DIAN processes them)
        wait for the DIAN, download the ZIPs and do CADI row by row."""
        started = time.monotonic()
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.times = defaultdict(list)
        self.row_times = defaultdict(dict)
        self._pending_times = []
        counts = {"ok": 0, "error": 0, "omitida": 0}
        folder = batch_folder(consolidado)
        stopped = set()  # OCs that failed or were skipped in pass 1
        with StateStore(config.STATE_DB) as store:
            self.prefetch_terceros(store, items)
            self.login_upfront(store, items)
            self.load_dupe_index(store, items)
            for stage in (self.register_stage, self.finish_stage):
                for row, _ in items:
                    if self.cancel.is_set():
                        self.ui.log("Proceso detenido por el usuario.")
                        break
                    if row.oc in stopped:
                        continue
                    out_dir = folder / safe_folder_name(row.provider_id, row.provider_name)
                    outcome = self._attempt(store, row, lambda r=row, o=out_dir: stage(store, r, o))
                    if outcome == "stop":
                        return self._stop_message + "\n" + self._close_timing(consolidado, started)
                    if outcome in ("error", "omitida"):
                        stopped.add(row.oc)
                        counts[outcome] += 1
                    elif outcome == "ok" and stage == self.finish_stage:
                        counts["ok"] += 1
        target = "terminadas (MyE + DIAN + CADI en revisión)" if self.cadi else "con ZIP descargado"
        summary = self._close_timing(consolidado, started)
        return (f"Proceso real terminado: {counts['ok']} {target}, {counts['error']} con error, "
                f"{counts['omitida']} omitidas por validación.\n{summary}")

    def _close_timing(self, consolidado: str, started: float) -> str:
        """Batch end (normal or stopped): totals for the message, the log and the report."""
        self.total_secs = time.monotonic() - started
        self.ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = self.timing_summary(self.total_secs)
        if self.row_times:
            summary += "\n" + self.row_times_text()
        log.info("Batch %s: %s", consolidado, summary)
        return summary

    def _attempt(self, store: StateStore, row: Row, work: Callable[[], str]) -> str:
        """Run one stage of one row; returns ok / continue / error / omitida / stop."""
        try:
            return self._attempt_inner(store, row, work)
        finally:
            self._flush_times(store, row)

    def _attempt_inner(self, store: StateStore, row: Row, work: Callable[[], str]) -> str:
        try:
            try:
                return work()
            except SessionExpired:
                self.ui.log("La sesión de MyE expiró; vuelva a iniciar sesión en el navegador.")
                self._mye_ready = False
                self.ensure_mye()
                return work()
        except (RegisteredUnknown, CadiUnknown) as exc:
            where = "MYE" if isinstance(exc, RegisteredUnknown) else "CADI"
            store.mark_failed(row.oc, row.provider_id, f"VERIFICAR EN {where}: {exc}")
            self.ui.row_update(row.oc, f"Verificar en {where}", str(exc), "invalid")
            self._stop_message = f"Proceso detenido en la OC {row.oc}: {exc}"
            return "stop"
        except (MyeError, DocumentsError, CadiError) as exc:
            detail = annotate(str(exc))  # adds where to fix it (Silogtran, Excel, MyE…) and what to do
            store.mark_failed(row.oc, row.provider_id, detail)
            log.warning("live oc=%s failed: %s", row.oc, detail)
            self.ui.row_update(row.oc, "Error", detail, "invalid")
            return "error"
        except Exception as exc:  # unexpected: stop, never guess
            log.exception("Unexpected error on OC %s", row.oc)
            state = store.get(row.oc, row.provider_id)
            if state is not None and state.status == DocStatus.PENDING and not state.de_number:
                # Nothing was registered yet (e.g. a timeout while filling/previewing): safe to skip this row.
                detail = annotate(f"inesperado: {exc}")
                store.mark_failed(row.oc, row.provider_id, detail)
                self.ui.row_update(row.oc, "Error", detail, "invalid")
                return "error"
            store.mark_failed(row.oc, row.provider_id, f"inesperado: {exc}")
            self.ui.row_update(row.oc, "Error", f"inesperado: {exc}", "invalid")
            self._stop_message = (f"Se detuvo el proceso por un error inesperado en la OC {row.oc}. "
                                  "Revise la carpeta debug y el estado en MyE/CADI.")
            return "stop"

    # ----------------------------------------------------------------- one row
    def process_row(self, store: StateStore, row: Row, out_dir: Path) -> str:
        """Both stages for a single row (used for one-off runs and tests)."""
        if not hasattr(self, "times"):
            self.times, self.row_times, self._pending_times = defaultdict(list), defaultdict(dict), []
        outcome = self.register_stage(store, row, out_dir)
        return outcome if outcome != "continue" else self.finish_stage(store, row, out_dir)

    def _state(self, store: StateStore, row: Row) -> DocState:
        state = store.get(row.oc, row.provider_id)
        if state is None:
            raise MyeError("La fila no está registrada en el estado; valide el Excel de nuevo.")
        return state

    def register_stage(self, store: StateStore, row: Row, out_dir: Path) -> str:
        """Pass 1: PENDING -> MYE_REGISTERED. Other rows are left for pass 2."""
        state = self._state(store, row)
        if state.last_ok_status != DocStatus.PENDING:
            return "continue"
        self.ensure_mye()
        with self.timed("Registro MyE", row.oc):
            de_number = self.register(row, out_dir, self._obs(store, row))
        if de_number is None:
            return "omitida"
        store.advance(row.oc, row.provider_id, DocStatus.MYE_REGISTERED, de_number=de_number)
        self.ui.row_update(row.oc, "Registrado", f"{de_number} · esperando DIAN", "")
        return "continue"

    def finish_stage(self, store: StateStore, row: Row, out_dir: Path) -> str:
        """Pass 2: DIAN -> ZIP -> CADI -> En revisión -> DONE."""
        state = self._state(store, row)
        step = state.last_ok_status
        if step == DocStatus.DONE:
            self.ui.row_update(row.oc, "Terminada", f"{state.de_number} · CADI {state.cadi_id}", "done")
            return "ok"
        if step == DocStatus.PENDING:  # not registered in pass 1 (validation stopped it)
            return "omitida"

        if step == DocStatus.MYE_REGISTERED:
            self.ensure_mye()
            self.ui.row_update(row.oc, "Esperando DIAN", state.de_number or "", "")
            with self.timed("Espera DIAN", row.oc):
                dian = self.docs.wait_dian_ok(state.de_number, row.provider_id, self.since)
            state = store.advance(row.oc, row.provider_id, DocStatus.DIAN_OK, dian_status=dian)
            step = state.last_ok_status

        if step == DocStatus.DIAN_OK:
            self.ensure_mye()
            self.ui.row_update(row.oc, "Descargando ZIP", state.de_number or "", "")
            with self.timed("Descarga ZIP", row.oc):
                pdf, xml = self.download(row, state.de_number, out_dir, self._obs(store, row))
            state = store.advance(row.oc, row.provider_id, DocStatus.ZIP_DOWNLOADED,
                                  pdf_path=str(pdf), xml_path=str(xml))
            self.ui.row_update(row.oc, "ZIP descargado", f"{state.de_number} · DIAN OK · {pdf.name}", "done")
            self.ui.log(f"OC {row.oc}: {state.de_number} aceptado por la DIAN. PDF: {pdf}")

        if self.cadi is None:
            return "ok"
        with self.timed("CADI", row.oc):
            final = self.cadi.run(store, row, state)
        if final is None:
            return "omitida"
        self.ui.row_update(row.oc, "Terminada", f"{final.de_number} · CADI {final.cadi_id} · En revisión", "done")
        self.ui.log(f"OC {row.oc}: CADI {final.cadi_id} en revisión. Fila terminada.")
        return "ok"

    def _obs(self, store: StateStore, row: Row) -> str:
        state = store.get(row.oc, row.provider_id)
        return row_observation(state, config.MYE_OBSERVATION_TEMPLATE, self.observation) if state \
            else self.observation

    def _check_duplicate_oc(self, row: Row, candidates: list[DocRow]) -> None:
        """A duplicate is the SAME purchase order, not the same provider and amount.

        1. CADI already has an invoice with this OC -> duplicate.
        2. Documents of the same provider and amount in MyE (MyE does not show the OC):
           their OC is taken from CADI (or from this batch). Another OC -> another service,
           go on. Same OC -> duplicate. OC unknown (not in CADI / no OC) -> stop for review.
        Raises MyeError to stop the row; never creates anything.
        """
        if self.cadi is None:  # no CADI to learn OCs from: keep the conservative rule
            if candidates:
                numbers = ", ".join(d.number for d in candidates)
                raise MyeError(f"Posible duplicado en MyE: {numbers} (mismo proveedor y valor). "
                               "No se crea otro; revise manualmente.")
            return
        portal = self.cadi.cadi
        same_oc = portal.find_by_oc(row.oc)
        if same_oc:
            found = ", ".join(f"factura {i.id} ({i.number}, {i.status or 'sin estado'})" for i in same_oc)
            raise MyeError(f"La OC {row.oc} ya está registrada en CADI: {found}. No se crea otro documento.")
        unknown = []
        for doc in candidates:
            known_oc = self.created_oc.get(doc.number)
            if known_oc is not None:
                ocs = {known_oc}
            else:
                ocs = {i.oc.strip() for i in portal.find_invoices(doc.number)}
            if row.oc in ocs:
                raise MyeError(f"La OC {row.oc} ya tiene el documento {doc.number} en MyE. "
                               "No se crea otro documento.")
            if not ocs or "" in ocs:
                unknown.append(doc.number)
            else:
                log.info("OC %s: %s del mismo proveedor y valor es de otra OC (%s); no es duplicado",
                         row.oc, doc.number, ", ".join(sorted(ocs)))
        if unknown:
            raise MyeError(f"Revisar: {', '.join(unknown)} es del mismo proveedor y valor, pero no está en "
                           "CADI o no tiene OC, así que no se puede saber si es esta OC "
                           f"({row.oc}). No se crea nada; verifique a mano.")

    def register(self, row: Row, out_dir: Path, observation: str | None = None) -> str | None:
        observation = observation or self.observation
        start = time.monotonic()
        index = getattr(self, "dupe_index", None)
        if self._overlap:
            self.mye.start_open_form()  # the slow form load proceeds while we check duplicates
        if index is not None:  # one search for the whole batch (see load_dupe_index)
            dupes = duplicates_in(index, row.provider_id, row.total)
        else:
            dupes = self.docs.possible_duplicates(row.provider_id, row.total, self.since)
        self._check_duplicate_oc(row, dupes)  # by OC: CADI + same provider/amount candidates
        log.info("TIMING oc=%s step=mye.buscar_duplicados secs=%.1f", row.oc, time.monotonic() - start)
        self.ui.row_update(row.oc, "Procesando…", "llenando MyE-invoice", "")

        def approve(preview: PreviewResult) -> bool:
            if self.auto_confirm:
                return True
            self.ui.row_update(row.oc, "Esperando confirmación", "vista previa OK", "")
            return self.ui.confirm(confirm_text(row, observation, preview), preview.pdf_path,
                                   "Registrar y enviar a la DIAN")

        de_number, preview = self.mye.register(row, observation, out_dir, approve)
        if de_number:
            self.created_oc[de_number] = row.oc  # its OC is known before it reaches CADI
            if index is not None:  # a later row of this batch must see it too
                index.append(DocRow(-1, de_number, f"NIT {row.provider_id}", "", row.total))
        self.acquirers[row.oc] = (self.mye.acquirer_name, self.mye.acquirer_surnames)
        if de_number:
            log.info("Registrado OC %s -> %s", row.oc, de_number)
            self.ui.row_update(row.oc, "Registrado", de_number, "")
            return de_number
        if not preview.ok:
            # A validation failure is an error with its reason (state, log and report).
            detail = "; ".join(preview.problems + preview.warnings)
            raise MyeError(f"No se registró: la vista previa no coincide con el Excel ({detail}). "
                           f"Borrador: {preview.pdf_path}")
        self.ui.row_update(row.oc, "No registrada", "cancelada por el usuario", "")
        return None

    def download(self, row: Row, de_number: str, out_dir: Path,
                 observation: str | None = None) -> tuple[Path, Path]:
        observation = observation or self.observation
        zip_path = self.docs.download_zip(de_number, row.provider_id, self.since, out_dir)
        pdf, xml = extract_pdf_and_xml(zip_path, out_dir / "_zip")
        final_pdf, final_xml = out_dir / f"{de_number}.pdf", out_dir / f"{de_number}.xml"
        shutil.copy2(pdf, final_pdf)
        shutil.copy2(xml, final_xml)
        name, surnames = self.acquirers.get(row.oc, (None, None))
        problems = check_pdf_text(pdf_text(final_pdf.read_bytes()), row, observation, de_number,
                                  acquirer_name=name, acquirer_surnames=surnames)
        if problems:
            raise MyeError(f"El PDF final de {de_number} no coincide: " + "; ".join(problems))
        return final_pdf, final_xml
