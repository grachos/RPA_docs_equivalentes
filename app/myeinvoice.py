"""MyE-invoice automation (Playwright, sync API) up to "Previsualizar".

Selectors were confirmed live on 2026-10-22 (scripts/probe_mye*.py). This module
NEVER clicks "Registrar" nor the "Confirmar" button of the preview dialog: that
belongs to phase 3 and will require explicit human confirmation per row.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, Response
from playwright.sync_api import TimeoutError as PWTimeout

from app import config
from app.models import Row
from app.mye_acquirer import ProviderNotFound, missing_address, pick_provider_suggestion
from app.mye_login import mye_login
from app.mye_tercero import apply_until_stable, mismatches, suggestion_prefix
from app.pdf_check import check_pdf_text, pdf_text
from app.terceros_api import Tercero, TerceroApiError, TercerosApi
from app.text_utils import fix_first_names, names_match, normalize_text, parse_total

log = logging.getLogger("doc_soporte")

SEL_NUMBERING = "#numeraciones"
SEL_IDENT = "#identificacionAdquiriente"
SEL_PERSON_TYPE = "#tiposPersonaAdquiriente"
SEL_NAMES = "#nombresAdquiriente"
SEL_SURNAMES = "#apellidosAdquiriente"
SEL_BUSINESS_NAME = "#razonSocialAdquiriente"
REQUIRED_ADDRESS = {
    "País": "#paisAdquiriente", "Departamento": "#departamentosAdquiriente",
    "Ciudad": "#ciudadesAdquiriente", "Dirección": "#direccionAdquiriente",
    "Código postal": "#codPostalAdquiriente",
}
ADD_BUTTON = "img.add_img_agregar_icons:visible"
# Preview dialog (#modalPrevisualizacionDocumento). "Confirmar" REGISTERS and sends to DIAN.
SEL_PREVIEW_CONFIRM = "#confirmarPrevisualizacion"
SEL_PREVIEW_CANCEL = "#cerrarPrevisualizacion"
LOGIN_WAIT_MS = 5 * 60_000


class MyeError(Exception):
    """A row cannot continue; the message is shown to the user."""


class SessionExpired(MyeError):
    """MyE redirected to the login page; log in again and retry the row."""


class RegisteredUnknown(MyeError):
    """Confirmar was pressed but the DE number could not be read. Never retry blindly."""


DE_NUMBER_RE = re.compile(r"\b(DE\d{6,})\b")


@dataclass
class PreviewResult:
    ok: bool
    pdf_path: Path | None = None
    warnings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


class MyeInvoice:
    """Drives one browser page. All calls must run in the thread that owns Playwright."""

    def __init__(self, page: Page, debug_dir: Path = config.DEBUG_DIR):
        self.page = page
        self.debug_dir = debug_dir
        # Name of the provider loaded for the current row (corrected Nombres + Apellidos).
        self.acquirer_name: str | None = None
        self.acquirer_surnames: str | None = None
        # Terceros API (Silogtran): completes a provider's missing address. The key is read
        # only when needed; without it the row stops asking to save it in Configuración.
        self.terceros: TercerosApi | None = TercerosApi()
        self.tercero: Tercero | None = None  # Silogtran data applied to the current form
        page.set_default_timeout(config.TIMEOUT_MS)

    # ------------------------------------------------------------------ helpers
    def _base_url(self) -> str:
        return config.MYE_BASE_URL

    def snapshot(self, tag: str) -> Path:
        """Screenshot + HTML for debugging a failure."""
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stem = self.debug_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{tag}"
        try:
            self.page.screenshot(path=f"{stem}.png", full_page=True)
            Path(f"{stem}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:  # browser may be gone
            log.exception("snapshot failed")
        return stem

    def _wait_js(self, fn: str, arg: object = None, timeout: int = 30_000) -> None:
        """Poll a JS predicate. The site's CSP forbids eval, so wait_for_function cannot be used."""
        waited = 0
        while not self.page.evaluate(fn, arg):
            if waited >= timeout:
                raise PWTimeout(f"Timeout {timeout} ms esperando: {fn[:80]}")
            self.page.wait_for_timeout(300)
            waited += 300

    def _set_select(self, selector: str, value: str) -> None:
        """Selects are select2 widgets: set the value and fire jQuery change."""
        self.page.evaluate(
            "([s, v]) => { $(s).val(v).trigger('change'); }", [selector, value])

    def _select_by_text(self, selector: str, text: str, *, prefix: bool = False) -> str:
        options = self.page.eval_on_selector_all(
            f"{selector} option", "os => os.map(o => ({v: o.value, t: o.text.trim()}))")
        if prefix:
            hits = [o for o in options if o["t"].startswith(text)]
        else:
            hits = [o for o in options if normalize_text(o["t"]) == normalize_text(text)]
        if len(hits) != 1:
            raise MyeError(f"Opción {text!r} no encontrada (o ambigua) en {selector}: "
                           f"{[o['t'] for o in options][:15]}")
        self._set_select(selector, hits[0]["v"])
        return hits[0]["v"]

    def _set_input(self, selector: str, value: str) -> None:
        loc = self.page.locator(f"{selector}:visible").first
        loc.fill(value)
        loc.dispatch_event("change")
        loc.dispatch_event("blur")

    def _set_value_silently(self, selector: str, value: str) -> None:
        """Set a value WITHOUT keystrokes/blur.

        Nombres/Apellidos/Razón Social have an autocomplete: typing there makes MyE
        load ANOTHER provider (seen live on 2026-10-22). Only a jQuery 'change' is fired.
        """
        self.page.evaluate(
            "([s, v]) => { $(s).val(v).trigger('change'); $('.ui-autocomplete').hide(); }",
            [selector, value])
        self.page.wait_for_timeout(500)

    def assert_acquirer(self, row: Row) -> None:
        """Hard guard: the form must still hold the Excel provider before previewing."""
        ident = self.page.eval_on_selector(SEL_IDENT, "e => (e.value || '').trim()")
        if ident != row.provider_id:
            raise MyeError(f"El formulario cambió de proveedor (identificación {ident!r}, "
                           f"esperada {row.provider_id}). Se detiene la fila.")

    def _selected_text(self, selector: str) -> str:
        return self.page.eval_on_selector(
            selector, "e => e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''")

    def _open_section(self, collapse_id: str) -> None:
        self.page.evaluate("id => $(id).collapse('show')", collapse_id)
        self._wait_js("id => document.querySelector(id).classList.contains('show')", collapse_id)
        self.page.wait_for_timeout(400)

    # -------------------------------------------------------------------- login
    def ensure_logged_in(self, notify: Callable[[str], None]) -> None:
        """Reuse the open session or log in (the human only types the captcha sum)."""
        if not mye_login(self.page, notify, LOGIN_WAIT_MS):
            raise MyeError("No se detectó el inicio de sesión en MyE-invoice.")

    # --------------------------------------------------------------------- form
    def start_open_form(self) -> None:
        """Start loading a fresh form without waiting, so other work (the duplicate search
        in another tab) can overlap the slowest step (~25 s). Finish with open_form()."""
        self.page.goto(self._base_url() + config.MYE_INVOICE_PAGE, wait_until="commit", timeout=90_000)
        self._form_started = True

    def open_form(self) -> None:
        if getattr(self, "_form_started", False):
            self._form_started = False
            self.page.wait_for_load_state("domcontentloaded", timeout=90_000)
        else:
            self.page.goto(self._base_url() + config.MYE_INVOICE_PAGE,
                           wait_until="domcontentloaded", timeout=90_000)
        if "entrada.jsp" not in self.page.url:
            raise SessionExpired("La sesión de MyE-invoice expiró.")
        self._wait_js("() => document.querySelectorAll('#numeraciones option').length > 1",
                      timeout=60_000)
        self._select_by_text(SEL_NUMBERING, config.MYE_DOCUMENT_TYPE)
        self._wait_js("() => document.querySelector('#identificacionAdquiriente') !== null")
        self.page.wait_for_timeout(1500)
        chosen = self._selected_text(SEL_NUMBERING)
        if chosen != config.MYE_DOCUMENT_TYPE:
            raise MyeError(f"Tipo de documento quedó en {chosen!r}")

    def fill_acquirer(self, row: Row) -> list[str]:
        """Pick the provider (or type a new one) and make it match Silogtran. Returns notices.

        Silogtran is the master data: tipo de documento/persona, names, addresses and the
        fixed delivery email are corrected in the form (app/mye_tercero.py). A provider that
        is not in MyE is filled in completely from Silogtran.
        """
        warnings: list[str] = []
        self.page.evaluate("$('#identificacionAdquiriente').first().closest('.collapse').collapse('show')")
        field = self.page.locator(f"{SEL_IDENT}:visible").first
        field.wait_for()
        self.tercero = tercero = self._silogtran(row)
        try:
            pick_provider_suggestion(self.page, field, row.provider_id,
                                     prefer=suggestion_prefix(tercero) if tercero else None)
            exists = True
        except ProviderNotFound as exc:
            if tercero is None:
                raise MyeError(f"{exc} Tampoco está en Silogtran.") from exc
            exists = False
            field.press("Tab")  # MyE computes the DV of the typed ID
        except ValueError as exc:
            raise MyeError(str(exc)) from exc
        if exists:
            self._wait_js(
                "() => (document.querySelector('#nombresAdquiriente')?.value || "
                "document.querySelector('#razonSocialAdquiriente')?.value || '').length > 0",
                timeout=20_000)
            self.page.wait_for_timeout(1500)  # city/department selects load in cascade

        if tercero is not None:
            changes = self._apply_silogtran(tercero)
            if not exists:
                warnings.append(f"Proveedor nuevo en MyE, diligenciado con datos de Silogtran ({tercero.nombre})")
            elif changes:
                warnings.append("Datos corregidos desde Silogtran: " + ", ".join(changes))
        else:
            missing = missing_address(self.page, REQUIRED_ADDRESS)
            if missing:
                raise MyeError("Faltan datos obligatorios del proveedor en MyE: " + ", ".join(missing)
                               + " y no está en Silogtran. Complételos en MyE-invoice.")

        person = self._selected_text(SEL_PERSON_TYPE)
        surnames = ""
        if normalize_text(person) == "PERSONA NATURAL":
            names = self.page.input_value(f"{SEL_NAMES} >> nth=0")
            surnames = self.page.input_value(f"{SEL_SURNAMES} >> nth=0")
            full = f"{fix_first_names(names, surnames)} {surnames}"
        else:
            full = self.page.input_value(f"{SEL_BUSINESS_NAME} >> nth=0")
        self.assert_acquirer(row)
        # The ID decides the provider; the Excel name may be incomplete (only a notice).
        self.acquirer_name, self.acquirer_surnames = " ".join(full.split()), surnames
        if not names_match(full, row.provider_name):
            warnings.append(f"Nombre del proveedor {full!r} distinto al del Excel {row.provider_name!r} "
                            "(se usa el del ID)")
        return warnings

    def _silogtran(self, row: Row) -> Tercero | None:
        """Provider data from the terceros API; None if it is not in Silogtran (or no API)."""
        if self.terceros is None:
            return None
        try:
            tercero = self.terceros.get(row.provider_id, with_names=True)
        except TerceroApiError as exc:
            raise MyeError(f"No se pudo consultar el proveedor en Silogtran: {exc}") from exc
        if tercero is None:
            log.info("Proveedor %s no está en Silogtran; se usan los datos de MyE", row.provider_id)
        return tercero

    def _apply_silogtran(self, tercero: Tercero, rounds: int = 3) -> list[str]:
        try:
            return apply_until_stable(self.page, tercero, rounds)
        except ValueError as exc:
            raise MyeError(f"No se pudieron aplicar los datos de Silogtran: {exc}") from exc

    def check_silogtran(self) -> None:
        """Hard guard before previewing: late MyE loads must not have undone Silogtran's data."""
        if self.tercero is None or not mismatches(self.page, self.tercero):
            return
        self._apply_silogtran(self.tercero, rounds=1)
        if wrong := mismatches(self.page, self.tercero):
            raise MyeError("El formulario no quedó con los datos de Silogtran: " + ", ".join(wrong))

    def fix_names(self) -> None:
        """Strip the surname MyE appends to 'Nombres' (natural persons only).

        Must run last: the provider's cascading data load rewrites 'Nombres'.
        """
        if normalize_text(self._selected_text(SEL_PERSON_TYPE)) != "PERSONA NATURAL":
            return
        names = self.page.input_value(f"{SEL_NAMES} >> nth=0")
        surnames = self.page.input_value(f"{SEL_SURNAMES} >> nth=0")
        fixed = fix_first_names(names, surnames)
        if fixed == names:
            return
        self._set_value_silently(SEL_NAMES, fixed)
        if self.page.input_value(f"{SEL_NAMES} >> nth=0") != fixed:
            raise MyeError(f"No se pudo corregir Nombres a {fixed!r}.")
        log.info("Nombres corregido: %r -> %r", names, fixed)

    def add_concept(self, total: int) -> None:
        self._open_section("#collapseThree")
        self.page.locator(f"#collapseThree {ADD_BUTTON}").first.click()
        modal = self.page.locator("#modalAddItem")
        modal.wait_for(state="visible")
        code = self.page.locator("#codigoAddItem")
        code.fill(config.MYE_CONCEPT_CODE)
        code.dispatch_event("change")
        code.dispatch_event("blur")
        self.page.wait_for_timeout(1200)  # the code may auto-fill the description
        self._set_input("#descripcionAddItem", config.MYE_CONCEPT_DESCRIPTION)
        self._set_input("#cantidadAddItem", "1")
        self._set_input("#unitarioAddItem", str(total))
        self.page.locator("#btnAgregarItem").click()
        try:
            modal.wait_for(state="hidden", timeout=10_000)
        except PWTimeout as exc:
            raise MyeError("El concepto no se pudo agregar (el formulario sigue abierto).") from exc
        rows = self.page.locator("#tableItems tbody tr").all_inner_texts()
        if not any(config.MYE_CONCEPT_CODE in r for r in rows):
            raise MyeError(f"No aparece el concepto {config.MYE_CONCEPT_CODE} en la tabla.")

    def add_po_attachment(self, oc: str) -> None:
        self._open_section("#collapseFour")
        self.page.locator(f"#collapseFour {ADD_BUTTON}").first.click()
        modal = self.page.locator("#modalAddAdjunto")
        modal.wait_for(state="visible")
        self._select_by_text("#tpAdjuntoAdd", config.MYE_ATTACHMENT_TYPE)
        self._set_input("#codigoAdjunto", oc)
        self._set_input("#descAdjunto", oc)
        self.page.locator("#btnAgregarAdjunto").click()
        try:
            modal.wait_for(state="hidden", timeout=10_000)
        except PWTimeout as exc:
            raise MyeError("El adjunto Orden de compra no se pudo agregar.") from exc
        rows = self.page.locator("#tableAdjuntos tbody tr").all_inner_texts()
        if not any(oc in r for r in rows):
            raise MyeError(f"No aparece el adjunto de la OC {oc} en la tabla.")

    def set_footer(self, row: Row, observation: str) -> None:
        self._select_by_text("#reportes", config.MYE_GRAPHIC_REPRESENTATION)
        self._set_input("#observaciones", observation)
        self._select_by_text("#metodosPago", config.MYE_PAYMENT_METHOD)
        self.page.wait_for_timeout(800)
        self._select_by_text("#mediosPago", config.MYE_PAYMENT_MEANS)
        self._set_input("#identificadorPago", row.provider_id)
        # Due date = issue date (the date part of the #fecha field, same format).
        issue = self.page.input_value("#fecha").split(" ")[0]
        self.page.evaluate(
            "v => { const e = document.querySelector('#fechaVencimientoPago'); e.value = v;"
            " $(e).trigger('change'); }", issue)

    def read_totals(self) -> dict[str, int]:
        cells = self.page.evaluate("""() => {
            const out = {};
            for (const tr of document.querySelectorAll('tr')) {
              const tds = [...tr.querySelectorAll('td')].map(t => t.innerText.trim());
              if (tds.length >= 2 && ['Subtotal', 'Total'].includes(tds[0])) out[tds[0]] = tds[tds.length - 1];
            }
            return out; }""")
        return {k: parse_total(v) for k, v in cells.items()}

    def check_totals(self, total: int) -> None:
        totals = self.read_totals()
        if totals.get("Subtotal") != total or totals.get("Total") != total:
            raise MyeError(f"Totales en pantalla {totals} no coinciden con el Excel ({total}).")

    # ------------------------------------------------------------------ preview
    def preview(self, row: Row, observation: str, out_dir: Path, keep_open: bool = False) -> PreviewResult:
        """Click Previsualizar and validate the draft PDF.

        The dialog is closed WITHOUT confirming, unless keep_open and the PDF is valid
        (then register() decides whether to press Confirmar).
        """
        def is_preview(resp: Response) -> bool:
            return "/Facturar" in resp.url and resp.request.method == "POST"

        with self.page.expect_response(is_preview, timeout=90_000) as info:
            self.page.locator("#previsualizarFactura").click()
            # MyE validates client-side first: on errors it shows "Datos inconsistentes" and sends nothing.
            self._raise_if_inconsistent()
        try:
            data = info.value.json()
        except (json.JSONDecodeError, Exception) as exc:
            raise MyeError("Respuesta inesperada de Previsualizar.") from exc

        if not data.get("success") or not data.get("pdf"):
            self._close_dialogs()
            msg = re.sub(r"<[^>]+>", " ", str(data.get("msg") or "sin detalle"))
            raise MyeError("MyE rechazó la previsualización (Datos inconsistentes): "
                           + " ".join(msg.split()))

        pdf_bytes = base64.b64decode(data["pdf"])
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"preview_{row.oc}.pdf"
        pdf_path.write_bytes(pdf_bytes)
        problems = check_pdf_text(pdf_text(pdf_bytes), row, observation,
                                  acquirer_name=self.acquirer_name, acquirer_surnames=self.acquirer_surnames)
        self.page.wait_for_timeout(1500)
        if problems or not keep_open:
            self._close_dialogs()
        return PreviewResult(ok=not problems, pdf_path=pdf_path, problems=problems)

    def _raise_if_inconsistent(self, wait_ms: int = 6000) -> None:
        """Right after clicking Previsualizar: if MyE shows its own "Datos inconsistentes" dialog the
        request is never sent, so fail now with MyE's message instead of timing out after 90 s."""
        modal = self.page.locator(".modal.show:visible", has_text="Datos inconsistentes")
        deadline = time.monotonic() + wait_ms / 1000
        while time.monotonic() < deadline:
            if modal.count():
                msg = " ".join((modal.first.inner_text() or "").split())
                self._close_dialogs()
                raise MyeError(f"MyE rechazó la previsualización: {msg}")
            if self.page.locator(f"{SEL_PREVIEW_CANCEL}:visible").count():
                return  # the preview dialog opened: the request went out
            self.page.wait_for_timeout(300)

    # ----------------------------------------------------------------- register
    def confirm_registration(self, row: Row) -> str:
        """Press "Confirmar" in the preview dialog (IRREVERSIBLE: creates the document and
        sends it to the DIAN) and return the DE number reported by MyE."""
        self.assert_acquirer(row)
        confirm = self.page.locator(f"{SEL_PREVIEW_CONFIRM}:visible")
        if confirm.count() != 1:
            raise MyeError("El diálogo de previsualización ya no está abierto; no se registró nada.")
        text = ""
        try:
            with self.page.expect_response(
                    lambda r: "/Facturar" in r.url and r.request.method == "POST",
                    timeout=180_000) as info:
                confirm.first.click()
            resp = info.value
            try:
                data = resp.json()
                data.pop("pdf", None)
                text = json.dumps(data, ensure_ascii=False)
            except Exception:
                text = resp.text()[:5000]
            log.info("Registro OC %s respuesta: %s", row.oc, text[:1000])
        except PWTimeout:
            log.warning("No llegó respuesta de Facturar tras Confirmar (OC %s)", row.oc)
        match = DE_NUMBER_RE.search(text)
        if not match:  # fall back to the on-screen message "Se ha generado ... número DE2522XX"
            self.page.wait_for_timeout(3000)
            match = DE_NUMBER_RE.search(self.page.inner_text("body"))
        if not match:
            self.snapshot(f"registro_{row.oc}")
            raise RegisteredUnknown(
                "Se pulsó Confirmar pero no se pudo leer el número del documento. NO reintente: "
                "verifique en MyE «Consultar Documentos Electrónicos».")
        return match.group(1)

    def register(self, row: Row, observation: str, out_dir: Path,
                 approve: Callable[[PreviewResult], bool]) -> tuple[str | None, PreviewResult]:
        """Fill, preview and validate; if `approve(preview)` returns True, press Confirmar.

        Returns (DE number or None if not registered, preview result).
        """
        result = self.prepare_and_preview(row, observation, out_dir, keep_open=True)
        if not result.ok:
            return None, result
        if not approve(result):
            self._close_dialogs()
            return None, result
        return self.confirm_registration(row), result

    def _close_dialogs(self) -> None:
        """Close any open modal using its dismiss/close control (never 'Confirmar')."""
        cancel = self.page.locator(f"{SEL_PREVIEW_CANCEL}:visible")
        if cancel.count():
            cancel.first.click()
            self.page.wait_for_timeout(600)
        for btn in self.page.locator(".modal.show [data-dismiss='modal']:visible, "
                                     ".modal.show button.close:visible").all():
            text = (btn.inner_text() or "").strip().lower()
            if "confirm" in text or "registr" in text:
                continue
            btn.click()
            self.page.wait_for_timeout(400)
            break
        self.page.evaluate("$('.modal.show').modal('hide')")

    # ------------------------------------------------------------------ one row
    def prepare_and_preview(self, row: Row, observation: str, out_dir: Path,
                            keep_open: bool = False) -> PreviewResult:
        """Fill everything and validate the preview (dry-run unless keep_open is used by register)."""
        mark = time.monotonic()

        def lap(step: str) -> None:  # per-substep timing in the log, to find slow parts
            nonlocal mark
            now = time.monotonic()
            log.info("TIMING oc=%s step=mye.%s secs=%.1f", row.oc, step, now - mark)
            mark = now

        try:
            self.open_form()
            lap("abrir_formulario")
            warnings = self.fill_acquirer(row)
            lap("proveedor")
            self.add_concept(row.total)
            self.add_po_attachment(row.oc)
            lap("concepto_y_adjunto")
            self.set_footer(row, observation)
            self.check_totals(row.total)
            self.fix_names()
            self.check_silogtran()
            self.assert_acquirer(row)
            lap("pie_y_validaciones")
            result = self.preview(row, observation, out_dir, keep_open=keep_open)
            lap("previsualizar")
            result.warnings = warnings + result.warnings
            return result
        except SessionExpired:
            raise
        except Exception:
            self.snapshot(f"mye_{row.oc}")
            raise
