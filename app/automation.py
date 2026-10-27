"""Browser worker: one thread owns Playwright for the whole app session.

Playwright's sync API must be used from the thread that created it, and keeping
the browser open between batches preserves the logins (captcha / email codes).
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from app import config
from app.cadi_batch import cadi_simulation
from app.instance import close_orphan_browsers
from app.live_batch import LiveBatch, batch_folder
from app.models import DocState, DocStatus, Row
from app.myeinvoice import MyeError, MyeInvoice, SessionExpired
from app.report import write_report
from app.state import StateStore
from app.zip_utils import safe_folder_name

log = logging.getLogger("doc_soporte")


@dataclass
class UiBridge:
    """Callbacks into the UI. Each one must be thread-safe (the UI uses `after`)."""

    log: Callable[[str], None]
    row_update: Callable[[str, str, str, str], None]  # oc, status, detail, tag
    confirm: Callable[[str, Path | None, str], bool]  # text, pdf, button label; blocks (REAL mode)
    finished: Callable[[str], None]


class _BatchStopped(Exception):
    """Stop a batch with a message for the user."""


class BrowserWorker:
    def __init__(self) -> None:
        self._jobs: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="browser")
        self._pw = None
        self._ctx = None
        self.cancel = threading.Event()
        self.busy = threading.Event()
        self._thread.start()

    # ---------------------------------------------------------- thread internals
    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            self.busy.set()
            try:
                job()
            except Exception:  # the job reports its own errors; this is a last resort
                log.exception("Browser job crashed")
            finally:
                self.busy.clear()
        self._close_browser()

    def _page(self):
        """Open (or reuse) the persistent Chromium profile."""
        if self._ctx is not None:
            try:
                if self._ctx.pages:
                    return self._ctx.pages[0]
            except Exception:
                self._ctx = None
        if self._pw is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
        try:
            self._ctx = self._launch()
        except Exception as exc:
            # A Chromium left open by a closed app window keeps the profile locked.
            if "existing browser session" not in str(exc) or not close_orphan_browsers():
                raise
            self._ctx = self._launch()
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _launch(self):
        extra = {"channel": config.BROWSER_CHANNEL} if config.BROWSER_CHANNEL else {}
        return self._pw.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE_DIR), headless=False, no_viewport=True,
            args=["--start-maximized"], accept_downloads=True, **extra)

    def _close_browser(self) -> None:
        for closer in (lambda: self._ctx and self._ctx.close(), lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:
                pass
        self._ctx = self._pw = None

    # ---------------------------------------------------------------- public API
    def submit(self, job: Callable[[], None]) -> None:
        self.cancel.clear()
        self._jobs.put(job)

    def shutdown(self, wait_seconds: float = 0) -> None:
        """Stop the worker; with wait_seconds, wait for the browser to close."""
        self.cancel.set()
        self._jobs.put(None)
        if wait_seconds:
            self._thread.join(wait_seconds)

    def preview_batch(self, items: list[tuple[Row, DocState]], consolidado: str,
                      observation: str, ui: UiBridge) -> None:
        """Phase 2 (simulation): fill MyE-invoice and validate the preview for each row."""
        self.submit(lambda: self._preview_batch(items, consolidado, observation, ui))

    def _tab(self, name: str):
        """Named tabs of the same browser (same sessions): "cadi" and "mye_docs".

        Separate tabs let slow page loads overlap, e.g. the MyE form loads in the first
        tab while the duplicate search runs in "mye_docs".
        """
        tabs = self.__dict__.setdefault("_tabs", {})
        page = tabs.get(name)
        if page is None or page.is_closed():
            page = self._page().context.new_page()
            tabs[name] = page
        return page

    def _cadi_page(self):
        return self._tab("cadi")

    def _docs_page(self):
        return self._tab("mye_docs")

    def _preview_batch(self, items, consolidado, observation, ui: UiBridge) -> None:
        """Simulation: MyE preview for pending rows, CADI form for rows with the ZIP downloaded."""
        with StateStore(config.STATE_DB) as store:
            fresh = [(row, store.get(row.oc, row.provider_id) or state) for row, state in items]
        mye_items = [(r, s) for r, s in fresh if s.last_ok_status == DocStatus.PENDING]
        cadi_items = [(r, s) for r, s in fresh if s.last_ok_status == DocStatus.ZIP_DOWNLOADED]
        for row, state in fresh:
            if (row, state) not in mye_items and (row, state) not in cadi_items:
                ui.row_update(row.oc, "Omitida", f"está en {state.last_ok_status.value}", "")
        ok = failed = 0
        if mye_items:
            try:
                ok, failed = self._mye_previews(mye_items, consolidado, observation, ui)
            except _BatchStopped as exc:
                ui.finished(str(exc))
                return
        cadi_ok = cadi_failed = 0
        if cadi_items and not self.cancel.is_set():
            try:
                cadi_ok, cadi_failed = cadi_simulation(self._cadi_page(), cadi_items, ui, self.cancel)
            except Exception as exc:
                log.exception("CADI simulation failed")
                ui.finished(f"La simulación de CADI se detuvo: {exc}")
                return
        parts = []
        if mye_items:
            parts.append(f"MyE: {ok} vista(s) previa(s) OK, {failed} con error")
        if cadi_items:
            parts.append(f"CADI: {cadi_ok} formulario(s) listo(s), {cadi_failed} con error")
        ui.finished("Simulación terminada. " + ("; ".join(parts) or "No había filas para simular")
                    + ". No se registró ni se cargó nada.")

    def _mye_previews(self, items, consolidado, observation, ui: UiBridge) -> tuple[int, int]:
        ok = failed = 0
        try:
            mye = MyeInvoice(self._page())
            ui.log("Abriendo MyE-invoice…")
            mye.ensure_logged_in(ui.log)
        except Exception as exc:
            log.exception("MyE login failed")
            raise _BatchStopped(f"No se pudo iniciar sesión en MyE-invoice: {exc}") from exc

        batch_dir = batch_folder(consolidado)
        for row, state in items:
            if self.cancel.is_set():
                ui.log("Proceso detenido por el usuario.")
                break
            ui.row_update(row.oc, "Procesando…", "llenando MyE-invoice", "")
            out_dir = batch_dir / safe_folder_name(row.provider_id, row.provider_name)
            try:
                try:
                    result = mye.prepare_and_preview(row, observation, out_dir)
                except SessionExpired:
                    ui.log("La sesión de MyE expiró; vuelva a iniciar sesión en el navegador.")
                    mye.ensure_logged_in(ui.log)
                    result = mye.prepare_and_preview(row, observation, out_dir)
            except MyeError as exc:
                failed += 1
                log.warning("preview oc=%s failed: %s", row.oc, exc)
                ui.row_update(row.oc, "Error", str(exc), "invalid")
                ui.log(f"Fila {row.excel_row} (OC {row.oc}): {exc}")
                continue
            except Exception as exc:  # unexpected: stop the batch, never guess
                log.exception("Unexpected error on OC %s", row.oc)
                ui.row_update(row.oc, "Error", f"inesperado: {exc}", "invalid")
                raise _BatchStopped(f"Se detuvo el proceso por un error inesperado en la OC {row.oc}. "
                                    "Revise la carpeta debug.") from exc
            detail = "Vista previa OK" if result.ok else "Revisar: " + "; ".join(result.problems)
            if result.warnings:
                detail += " | Aviso: " + "; ".join(result.warnings)
            log.info("preview oc=%s ok=%s detail=%s pdf=%s", row.oc, result.ok, detail, result.pdf_path)
            ui.row_update(row.oc, "Simulación OK" if result.ok else "Revisar", detail,
                          "done" if result.ok else "invalid")
            ui.log(f"OC {row.oc}: {detail}. PDF: {result.pdf_path}")
            ok += result.ok
            failed += not result.ok
        return ok, failed

    def live_batch(self, items: list[tuple[Row, DocState]], consolidado: str, observation: str,
                   cadi_observation: str, corte_date: date, auto_confirm: bool, ui: UiBridge) -> None:
        """REAL: MyE + DIAN + ZIP, then CADI (create + En revisión) for each row."""
        self.submit(lambda: self._live_batch(items, consolidado, observation, cadi_observation,
                                             corte_date, auto_confirm, ui))

    def _live_batch(self, items, consolidado, observation, cadi_observation, corte_date,
                    auto_confirm, ui) -> None:
        batch = None
        try:
            batch = LiveBatch(self._page(), ui, observation, corte_date, auto_confirm, self.cancel,
                              cadi_page=self._cadi_page, cadi_observation=cadi_observation,
                              docs_page=self._docs_page())
            message = batch.run(items, consolidado)
        except Exception as exc:
            log.exception("Live batch failed")
            message = f"El proceso real se detuvo: {exc}"
        try:
            with StateStore(config.STATE_DB) as store:
                summary = batch.summary_rows() if batch is not None and hasattr(batch, "total_secs") else None
                _, xlsx = write_report(store.list(consolidado=consolidado), config.REPORTS_DIR, consolidado,
                                       summary)
            message += f"\n\nReporte: {xlsx}"
        except Exception:
            log.exception("Report failed")
        ui.finished(message)
