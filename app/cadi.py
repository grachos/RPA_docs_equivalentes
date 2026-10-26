"""CADI automation (Playwright, sync API): login, duplicate check and the invoice form.

Selectors confirmed live on 2026-10-23 (scripts/probe_cadi.py). Phase 4 fills the
form and STOPS before "Cargar". This module never clicks "Anular factura",
"Enviar a Incidencias" or "Editar".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from app import config
from app.models import Row
from app.text_utils import normalize_text, parse_total

log = logging.getLogger("doc_soporte")

LOGIN_WAIT_MS = 10 * 60_000
SUBMIT_WAIT_MS = 3 * 60_000  # "Cargar" uploads the PDF before redirecting
SUBMIT = "input[type=submit][value='Cargar']"

# Grid rows of the invoices list (DevExtreme), mapped by header name because the
# column order differs between views ("Todas", "Abierto"...).
LIST_ROWS_JS = r"""
() => {
  const cells = tr => [...tr.querySelectorAll('td, th')].map(c => c.innerText.trim());
  const header = [...document.querySelectorAll('tr')].map(cells).find(r => r.includes('#Factura') && r.includes('ID'));
  if (!header) return [];
  const col = name => header.indexOf(name);
  const get = (r, name) => (col(name) >= 0 ? r[col(name)] || '' : '');
  return [...document.querySelectorAll('tr')].map(cells)
    .filter(r => r.length === header.length && /^\d+$/.test(get(r, 'ID')))
    .map(r => ({id: get(r, 'ID'), number: get(r, '#Factura'), type: get(r, 'Tipo'),
                nit: get(r, 'NIT Proveedor'), provider: get(r, 'Proveedor'), oc: get(r, '#Orden de compra'),
                status: get(r, 'Estado'), total: get(r, 'Valor total') || get(r, 'Valor')}));
}
"""

# Details page: label/value tables ("ID | Numero de factura | ... | Estado" then the values row).
DETAILS_JS = r"""
() => {
  const out = {};
  for (const table of document.querySelectorAll('table')) {
    const rows = [...table.querySelectorAll('tr')].map(tr => [...tr.querySelectorAll('td, th')].map(c => c.innerText.trim()));
    for (let i = 0; i + 1 < rows.length; i++) {
      const [labels, values] = [rows[i], rows[i + 1]];
      if (labels.length > 2 && labels.length === values.length
          && (labels.includes('Estado') || labels.includes('Centro')))
        labels.forEach((l, k) => { if (l && !(l in out)) out[l] = values[k] || ''; });
    }
  }
  return out;
}
"""


class CadiError(Exception):
    """A row cannot continue in CADI; the message is shown to the user."""


class CadiUnknown(CadiError):
    """"Cargar" was pressed but the result could not be confirmed. Never retry blindly."""


REVIEW_MODAL = "#in_review_modal"
INVOICE_ID_RE = re.compile(r"invoices/details\?i=(\d+)")


@dataclass
class CadiInvoice:
    id: str
    number: str
    type: str
    nit: str
    provider: str
    oc: str
    status: str
    total: str


@dataclass
class CadiForm:
    """What the app will submit for one documento soporte."""

    invoice_number: str
    supplier_nit: str
    order_reference: str
    issue_date: date
    total: int
    pdf: Path
    xml: Path | None = None


def build_form(row: Row, de_number: str, issue_date: date, pdf: Path, xml: Path | None = None) -> CadiForm:
    if len(de_number) > config.CADI_INVOICE_NUMBER_MAX_LEN:
        raise CadiError(f"El número {de_number} supera {config.CADI_INVOICE_NUMBER_MAX_LEN} caracteres.")
    if not pdf.exists():
        raise CadiError(f"No existe el PDF a adjuntar: {pdf}")
    return CadiForm(de_number, row.provider_id, row.oc, issue_date, row.total, pdf, xml)


class Cadi:
    def __init__(self, page: Page, debug_dir: Path = config.DEBUG_DIR):
        self.page = page
        self.debug_dir = debug_dir
        page.set_default_timeout(config.TIMEOUT_MS)

    # ------------------------------------------------------------------ helpers
    def snapshot(self, tag: str) -> None:
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stem = self.debug_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{tag}"
        try:
            self.page.screenshot(path=f"{stem}.png", full_page=True)
            Path(f"{stem}.html").write_text(self.page.content(), encoding="utf-8")
        except Exception:
            log.exception("CADI snapshot failed")

    def _logged_in(self) -> bool:
        url = self.page.url
        return url.startswith(config.CADI_HOME_URL) and "login" not in url

    def _select_by_text(self, selector: str, text: str) -> None:
        options = self.page.eval_on_selector_all(
            f"{selector} option", "os => os.map(o => ({v: o.value, t: o.text.trim()}))")
        hits = [o for o in options if normalize_text(o["t"]) == normalize_text(text)]
        if len(hits) != 1:
            raise CadiError(f"Opción {text!r} no encontrada (o repetida) en {selector}.")
        self.page.select_option(selector, value=hits[0]["v"])

    # -------------------------------------------------------------------- login
    def ensure_logged_in(self, notify: Callable[[str], None]) -> None:
        """Reuse the session; otherwise fill document/password. The human solves the
        Cloudflare check, presses the button and types the code sent by email."""
        self.page.goto(config.CADI_HOME_URL, wait_until="domcontentloaded", timeout=90_000)
        if self._logged_in():
            return
        if "login" not in self.page.url:
            self.page.goto(config.CADI_LOGIN_URL, wait_until="domcontentloaded", timeout=90_000)
        try:
            # The login page also holds a hidden "restore password" form with another
            # input[name=username] (#restore-username): only use the visible login fields.
            self.page.locator("input[name=username]:visible:not(#restore-username)").first.fill(
                config.get_credential("CADI_DOCUMENT"), timeout=15_000)
            self.page.locator("input[name=password]:visible").first.fill(
                config.get_credential("CADI_PASSWORD"), timeout=15_000)
            filled = True
        except Exception as exc:  # never stop the batch for autofill: the human can type it
            log.warning("CADI login autofill skipped: %s", exc)
            filled = False
        self.page.bring_to_front()
        notify("CADI: resuelva la verificación de Cloudflare, pulse «INICIAR SESIÓN» y escriba el "
               "código que llega al correo." if filled else
               "CADI: inicie sesión en el navegador (documento, contraseña, verificación y código).")
        waited = 0
        while not self._logged_in():
            if waited >= LOGIN_WAIT_MS:
                raise CadiError("No se detectó el inicio de sesión en CADI.")
            self.page.wait_for_timeout(2000)
            waited += 2000

    # ------------------------------------------------------------ duplicates
    def find_invoices(self, number: str) -> list[CadiInvoice]:
        """Invoices whose number is exactly `number` (all states)."""
        return [i for i in self._filter_list("#Factura", number) if i.number == number]

    def find_by_oc(self, oc: str) -> list[CadiInvoice]:
        """Invoices whose purchase order (#Orden de compra) is exactly `oc` (all states)."""
        return [i for i in self._filter_list("#Orden de compra", oc) if i.oc.strip() == oc]

    def _filter_list(self, column: str, value: str) -> list[CadiInvoice]:
        """Type `value` in the grid's filter of `column` (Facturas > Todas) and read the rows."""
        self.page.goto(config.CADI_LIST_URL, wait_until="domcontentloaded", timeout=90_000)
        # The grid is drawn by script after the page loads: wait for its header.
        for _ in range(30):
            if self.page.evaluate("t => [...document.querySelectorAll('td, th')]"
                                  ".some(c => c.innerText.trim() === t)", column):
                break
            self.page.wait_for_timeout(500)
        self.page.wait_for_timeout(500)
        # The grid has a filter row under the headers: tag the input of that column.
        found = self.page.evaluate("""t => {
            const head = [...document.querySelectorAll('td, th')].find(c => c.innerText.trim() === t);
            if (!head) return false;
            const table = head.closest('table'), col = head.cellIndex;
            const row = [...table.querySelectorAll('tr')].find(tr => tr.querySelector('input'));
            const input = row && row.cells[col] && row.cells[col].querySelector('input');
            if (!input) return false;
            document.querySelectorAll('[data-app-filter]').forEach(e => e.removeAttribute('data-app-filter'));
            input.setAttribute('data-app-filter', 'column');
            return true; }""", column)
        if not found:
            raise CadiError(f"No se encontró el filtro de la columna {column} en la lista de facturas.")
        filt = self.page.locator("input[data-app-filter='column']")
        filt.fill(value)
        filt.press("Enter")
        self.page.wait_for_timeout(3500)
        rows = self.page.evaluate(LIST_ROWS_JS)
        return [CadiInvoice(r["id"], r["number"], r["type"], r["nit"], r["provider"], r["oc"],
                            r["status"], r["total"]) for r in rows]

    # -------------------------------------------------------------------- form
    def fill_create_form(self, form: CadiForm) -> dict[str, str]:
        """Fill Contabilidad > Facturas > Crear and verify it. Does NOT press "Cargar"."""
        self.page.goto(config.CADI_CREATE_URL, wait_until="domcontentloaded", timeout=90_000)
        self.page.locator("#type_fac").wait_for()
        self._select_by_text("#type_fac", config.CADI_INVOICE_KIND)
        self.page.locator("#type_id").wait_for(state="visible")
        self.page.fill("#invoice_number", form.invoice_number)
        self.page.fill("#supplier_nit", form.supplier_nit)
        self._select_by_text("#company_id", config.CADI_COMPANY)
        self.page.fill("#order_reference", form.order_reference)
        self.page.fill("#date", form.issue_date.isoformat())
        self.page.fill("#expiration_date", form.issue_date.isoformat())
        self._select_by_text("#type_id", config.CADI_INVOICE_TYPE)
        self.page.fill("#iva", "0")
        self.page.fill("#value_simple", str(form.total))
        self.page.locator("#value_simple").dispatch_event("change")
        self.page.locator("#value_simple").blur()
        self.page.wait_for_timeout(600)
        # CADI computes "Valor total" from "Valor sin iva" + IVA; typing there appends digits.
        current = self.page.input_value("#value")
        try:
            computed = parse_total(current)
        except ValueError:
            computed = None
        if computed != form.total:
            self.page.evaluate("v => { const e = document.getElementById('value'); e.value = v;"
                               " e.dispatchEvent(new Event('change', {bubbles: true})); }", str(form.total))
        for flag in ("#has_cp", "#has_import", "#has_purse"):
            self._select_by_text(flag, "No")
        self.page.set_input_files("#support_invoice", str(form.pdf))
        if form.xml:
            self.page.set_input_files("#support_xml", str(form.xml))
        self.page.wait_for_timeout(800)
        return self.verify_form(form)

    def verify_form(self, form: CadiForm) -> dict[str, str]:
        """Read the form back; raise if anything differs from what must be submitted."""
        values = self.page.evaluate("""() => {
            const v = id => { const e = document.getElementById(id); if (!e) return null;
              return e.tagName === 'SELECT' ? (e.options[e.selectedIndex] || {text: ''}).text.trim() : e.value; };
            const f = document.getElementById('support_invoice');
            return {type_fac: v('type_fac'), invoice_number: v('invoice_number'), supplier_nit: v('supplier_nit'),
                    company: v('company_id'), order_reference: v('order_reference'), date: v('date'),
                    expiration_date: v('expiration_date'), type: v('type_id'), iva: v('iva'),
                    value_simple: v('value_simple'), value: v('value'), cp: v('has_cp'),
                    import: v('has_import'), purse: v('has_purse'), reviewed_by: v('reviewed_by') || '',
                    pdf: f && f.files.length ? f.files[0].name : ''}; }""")
        expected = {
            "type_fac": config.CADI_INVOICE_KIND, "invoice_number": form.invoice_number,
            "supplier_nit": form.supplier_nit, "company": config.CADI_COMPANY,
            "order_reference": form.order_reference, "date": form.issue_date.isoformat(),
            "expiration_date": form.issue_date.isoformat(), "type": config.CADI_INVOICE_TYPE,
            "iva": "0", "cp": "No", "import": "No", "purse": "No", "pdf": form.pdf.name,
            # The review is done afterwards with the "En revisión" button (user decision).
            "reviewed_by": "",
        }
        diffs = [f"{k}: {values.get(k)!r} ≠ {v!r}" for k, v in expected.items()
                 if normalize_text(str(values.get(k) or "")) != normalize_text(v)]
        for key in ("value_simple", "value"):
            try:
                if parse_total(values.get(key) or "") != form.total:
                    diffs.append(f"{key}: {values.get(key)!r} ≠ {form.total}")
            except ValueError:
                diffs.append(f"{key}: vacío")
        if diffs:
            self.snapshot(f"cadi_form_{form.order_reference}")
            raise CadiError("El formulario de CADI no quedó como se esperaba: " + "; ".join(diffs))
        return values

    def screenshot_form(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=True)
        return path

    # ----------------------------------------------------------- REAL actions
    def submit_create(self, form: CadiForm) -> str:
        """Press "Cargar" (IRREVERSIBLE: creates the invoice) and return the CADI invoice id."""
        self.verify_form(form)
        submit = self.page.locator(f"{SUBMIT}:visible")
        if submit.count() != 1:
            raise CadiError("No se encontró el botón «Cargar»; no se creó nada.")
        # CADI uploads the PDF before redirecting; that took > 30 s once (2026-10-23), so the
        # click must not wait for the navigation with the default timeout.
        try:
            submit.first.click(no_wait_after=True)
            self.page.wait_for_url(re.compile(r"invoices/details\?i=\d+"), timeout=SUBMIT_WAIT_MS)
            match = INVOICE_ID_RE.search(self.page.url)
            if match:
                return match.group(1)
        except PWTimeout:
            log.warning("CADI no redirigió al detalle tras Cargar (%s)", form.invoice_number)
        # No redirect: find out whether the invoice exists before saying anything.
        self.snapshot(f"cadi_cargar_{form.invoice_number}")
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=SUBMIT_WAIT_MS)
        except PWTimeout:
            pass
        found = self.find_invoices(form.invoice_number)
        if len(found) == 1:
            return found[0].id
        raise CadiUnknown(f"Se pulsó «Cargar» para {form.invoice_number} pero no se pudo confirmar "
                          f"la factura en CADI ({len(found)} encontradas). Verifique a mano; NO reintente.")

    def open_details(self, invoice_id: str) -> dict[str, str]:
        self.page.goto(f"{config.CADI_LIST_URL}/details?i={invoice_id}",
                       wait_until="domcontentloaded", timeout=90_000)
        self.page.wait_for_timeout(1500)
        details = self.page.evaluate(DETAILS_JS)
        if details.get("ID") != invoice_id:
            raise CadiError(f"No se pudo leer el detalle de la factura CADI {invoice_id}.")
        return details

    def fill_review(self, invoice_id: str, oc: str, observation: str) -> None:
        """Open "En revisión" and fill its modal. Does NOT press "Crear"."""
        details = self.open_details(invoice_id)
        if normalize_text(details.get("Estado")) != "ABIERTO":
            raise CadiError(f"La factura {invoice_id} está en «{details.get('Estado')}», no en «Abierto».")
        self.page.locator("a.button.yellow:visible").filter(has_text="En revisión").first.click()
        modal = self.page.locator(REVIEW_MODAL)
        modal.wait_for(state="visible")
        order = modal.locator("#order_reference")
        if order.input_value().strip() != oc:
            order.fill(oc)
        self._select_by_text(f"{REVIEW_MODAL} #center_id", config.CADI_CENTER)
        if not config.CADI_REVIEWER:
            raise CadiError("Falta CADI_REVIEWER en .env («<cédula> - <NOMBRE>» del revisor en CADI).")
        self._select_by_text(f"{REVIEW_MODAL} #reviewed_by", config.CADI_REVIEWER)
        modal.locator("#observations").fill(observation)
        values = self.page.evaluate("""s => { const m = document.querySelector(s);
            const t = id => { const e = m.querySelector('#' + id); return e.tagName === 'SELECT'
                ? (e.options[e.selectedIndex] || {text: ''}).text.trim() : e.value.trim(); };
            return {order: t('order_reference'), center: t('center_id'), reviewer: t('reviewed_by'),
                    observations: t('observations')}; }""", REVIEW_MODAL)
        expected = {"order": oc, "center": config.CADI_CENTER, "reviewer": config.CADI_REVIEWER,
                    "observations": observation}
        diffs = [f"{k}: {values[k]!r} ≠ {v!r}" for k, v in expected.items()
                 if normalize_text(values[k]) != normalize_text(v)]
        if diffs:
            raise CadiError("El formulario «En revisión» no quedó como se esperaba: " + "; ".join(diffs))

    def submit_review(self, invoice_id: str, oc: str, total: int) -> dict[str, str]:
        """Press "Crear" in the "En revisión" modal (IRREVERSIBLE) and verify the result."""
        create = self.page.locator(f"{REVIEW_MODAL} input[type=submit][value='Crear']:visible")
        if create.count() != 1:
            raise CadiError("El formulario «En revisión» ya no está abierto; no se envió nada.")
        try:
            with self.page.expect_navigation(timeout=SUBMIT_WAIT_MS, wait_until="domcontentloaded"):
                create.first.click(no_wait_after=True)
        except PWTimeout:
            log.warning("CADI no recargó tras «En revisión» (%s); se verifica el detalle", invoice_id)
        return self.verify_reviewed(invoice_id, oc, total)

    def verify_reviewed(self, invoice_id: str, oc: str, total: int) -> dict[str, str]:
        details = self.open_details(invoice_id)
        problems = []
        if normalize_text(details.get("Estado")) != "EN REVISION":
            problems.append(f"Estado {details.get('Estado')!r}")
        if normalize_text(details.get("Centro")) != normalize_text(config.CADI_CENTER):
            problems.append(f"Centro {details.get('Centro')!r}")
        if details.get("Numero de orden") != oc:
            problems.append(f"Número de orden {details.get('Numero de orden')!r}")
        try:
            if parse_total(details.get("Valor total", "").replace("$", "")) != total:
                problems.append(f"Valor total {details.get('Valor total')!r}")
        except ValueError:
            problems.append("Valor total ilegible")
        if problems:
            self.snapshot(f"cadi_review_{invoice_id}")
            raise CadiError(f"La factura CADI {invoice_id} no quedó como se esperaba: " + "; ".join(problems))
        return details
