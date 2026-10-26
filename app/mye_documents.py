"""MyE-invoice "Consultar Documentos Electrónicos": duplicate check, DIAN status, ZIP download.

Read-only page usage. The toolbar also has destructive/irreversible icons
("Eliminar Selección", "Enviar a la DIAN", "Regenerar UBL", "Generar Aceptación
tácita"...): this module only ever clicks Buscar, a row checkbox and
"Descargar AttachedDocument (Zip)".
"""
from __future__ import annotations

import logging
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from playwright.sync_api import Page

from app import config
from app.myeinvoice import SessionExpired
from app.text_utils import de_number_from_filename, normalize_text, parse_total

log = logging.getLogger("doc_soporte")

ZIP_ICON = "[data-tippy-content='Descargar AttachedDocument (Zip)']:visible"
NUMBERING_OPTION = "8759 - DE25"

ROWS_JS = """
() => [...document.querySelectorAll('#tablaDocElectronicos tbody tr')].map((tr, i) => {
  const tds = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
  const n = tds.findIndex(t => /^DE\\d{6,}$/.test(t));
  if (n < 0) return null;
  const money = tds.filter(t => /^\\$/.test(t));
  return {index: i, number: tds[n], acquirer: tds[n + 2] || '', dian: tds[n + 3] || '',
          value: money.length ? money[money.length - 1] : ''};
}).filter(Boolean)
"""


@dataclass
class DocRow:
    index: int
    number: str
    acquirer: str
    dian: str
    value: int


class DocumentsError(Exception):
    pass


class MyeDocuments:
    def __init__(self, page: Page):
        self.page = page

    def _wait_idle(self, timeout_s: int = 90) -> None:
        self.page.wait_for_timeout(1500)
        for _ in range(timeout_s):
            busy = self.page.evaluate("""() => [...document.querySelectorAll('.dataTables_processing')]
                .some(e => e.offsetWidth && getComputedStyle(e).display !== 'none')""")
            if not busy:
                self.page.wait_for_timeout(800)
                return
            self.page.wait_for_timeout(1000)
        raise DocumentsError("La consulta de documentos no terminó de cargar.")

    def search(self, provider_id: str, since: date, until: date | None = None) -> list[DocRow]:
        """Documento Soporte (8759) of a provider registered between since and until."""
        key = (provider_id, since, until or date.today())
        if key == getattr(self, "_last_search", None) and "administrarDocElectronicos" in self.page.url:
            # Same filters already on screen (e.g. polling the DIAN status): just search again,
            # without reloading the whole page.
            self.page.locator("#btnSearch").click()
            self._wait_idle()
            return self._read_rows(provider_id)
        self._open_and_filter(provider_id, since, until)
        self._last_search = key
        return self._read_rows(provider_id)

    def _open_and_filter(self, provider_id: str, since: date, until: date | None) -> None:
        """Open the query page, set dates/numbering/identification ("" = all) and search."""
        # Runs in its own tab (it may start blank): use the fixed base URL.
        self.page.goto(config.MYE_BASE_URL + config.MYE_DOCS_PAGE, wait_until="domcontentloaded",
                       timeout=90_000)
        if "entrada.jsp" not in self.page.url:
            raise SessionExpired("La sesión de MyE-invoice expiró.")
        self.page.locator("#btnSearch").wait_for(timeout=60_000)
        for _ in range(60):  # catalogs load asynchronously
            if self.page.evaluate("num => [...document.querySelectorAll('#cmbNume option')]"
                                  ".some(o => o.text.trim() === num)", NUMBERING_OPTION):
                break
            self.page.wait_for_timeout(500)
        self.page.evaluate(
            """([a, b, num]) => {
                for (const [id, v] of [['fechaInicialReg', a], ['fechaFinalReg', b]]) {
                    const e = document.querySelector('#' + id); e.value = v; $(e).trigger('change'); }
                const opt = [...document.querySelectorAll('#cmbNume option')].find(o => o.text.trim() === num);
                if (!opt) throw new Error('numeración no encontrada');
                $('#cmbNume').val(opt.value).trigger('change'); }""",
            [since.isoformat(), (until or date.today()).isoformat(), NUMBERING_OPTION])
        ident = self.page.locator("#identificacion:visible").first
        ident.fill("")  # the browser may autofill the MyE username here
        if provider_id:
            ident.fill(provider_id)
        self.page.locator("#btnSearch").click()
        self._wait_idle()

    def _read_rows(self, provider_id: str | None) -> list[DocRow]:
        """Rows on screen; with provider_id, only that provider (exact ID, not a prefix)."""
        out = []
        for r in self.page.evaluate(ROWS_JS):
            try:
                value = parse_total(r["value"].replace("$", "").replace("\xa0", ""))
            except ValueError:
                value = 0
            if provider_id is None or acquirer_id(r["acquirer"]) == provider_id:
                out.append(DocRow(r["index"], r["number"], r["acquirer"], r["dian"], value))
        return out

    def search_all(self, since: date, max_pages: int = 30) -> list[DocRow] | None:
        """ALL Documento Soporte registered since `since` (every page), for one duplicate
        check per batch. Returns None if it cannot confirm it read every document; the
        caller then falls back to the per-provider search."""
        self._open_and_filter("", since, None)
        self._last_search = None
        length = self.page.locator("select[name='tablaDocElectronicos_length']")
        if length.count():
            length.select_option("200")
            self._wait_idle()
        found: dict[str, DocRow] = {}
        for _ in range(max_pages):
            for d in self._read_rows(None):
                found[d.number] = d
            total = self._total_count()
            if total is not None and len(found) >= total:
                break
            nxt = self.page.locator("#tablaDocElectronicos_next")
            if not nxt.count() or "disabled" in (nxt.get_attribute("class") or ""):
                break
            nxt.click()
            self._wait_idle()
        total = self._total_count()
        if total is None or len(found) != total:
            log.warning("Búsqueda de duplicados por lote incompleta (%s de %s); se usa la búsqueda por fila",
                        len(found), total)
            return None
        log.info("Búsqueda de duplicados por lote: %s documentos desde %s", total, since)
        return list(found.values())

    def _total_count(self) -> int | None:
        """Total from the table info text, e.g. "(1 al 25) de 182"."""
        info = self.page.locator("#tablaDocElectronicos_info")
        text = info.inner_text() if info.count() else ""
        # MyE appends " 0 filas seleccionadas", so the total is not at the end of the text.
        match = (re.search(r"\)\s*de\s+([\d.,]+)", text)
                 or re.search(r"\bde\s+([\d.,]+)", text))
        return int(re.sub(r"\D", "", match.group(1))) if match else None

    def possible_duplicates(self, provider_id: str, total: int, since: date) -> list[DocRow]:
        """Existing documents of the same provider and amount (safe-side duplicate check)."""
        return duplicates_in(self.search(provider_id, since), provider_id, total)

    def find(self, de_number: str, provider_id: str, since: date) -> DocRow | None:
        return next((d for d in self.search(provider_id, since) if d.number == de_number), None)

    def wait_dian_ok(self, de_number: str, provider_id: str, since: date) -> str:
        """Poll until "Procesado Correctamente"; raise on any other final state or after
        DIAN_MAX_WAIT_SECONDS. The send itself is left to MyE's monitor."""
        last = ""
        deadline = time.monotonic() + config.DIAN_MAX_WAIT_SECONDS
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            doc = self.find(de_number, provider_id, since)
            last = doc.dian if doc else "(no aparece aún)"
            status = normalize_text(last)
            if status.startswith(normalize_text(config.MYE_DIAN_OK_STATUS)):
                return last.split("\n")[0].strip()
            pending = not doc or any(s in status for s in (
                "LISTO PARA ENVIAR", "SIN ENVIAR", "ENVIADO", "ENVIO POSPUESTO", "DESCONOCIDO"))
            if not pending:
                raise DocumentsError(f"La DIAN no aceptó {de_number}: {last}")
            log.info("DIAN %s attempt %s: %s", de_number, attempt, last.split("\n")[0])
            self.page.wait_for_timeout(config.DIAN_POLL_SECONDS * 1000)
        raise DocumentsError(f"{de_number} sigue en «{last.splitlines()[0] if last else '?'}» después de "
                             f"{config.DIAN_MAX_WAIT_SECONDS // 60} min; al reintentar se seguirá esperando "
                             "(no se registra de nuevo).")

    def download_zip(self, de_number: str, provider_id: str, since: date, out_dir: Path) -> Path:
        doc = self.find(de_number, provider_id, since)
        if not doc:
            raise DocumentsError(f"No se encontró {de_number} para descargar.")
        row = self.page.locator("#tablaDocElectronicos tbody tr").nth(doc.index)
        checkbox = row.locator("input[type=checkbox]")
        (checkbox.first if checkbox.count() else row.locator("td").nth(1)).click()
        self.page.wait_for_timeout(800)
        selected = self.page.evaluate("""() => [...document.querySelectorAll('#tablaDocElectronicos tbody tr')]
            .filter(tr => tr.classList.contains('selected') || tr.querySelector('input[type=checkbox]:checked'))
            .map(tr => tr.innerText)""")
        if len(selected) != 1 or de_number not in selected[0]:
            raise DocumentsError(f"No se pudo seleccionar solo {de_number} (seleccionadas: {len(selected)}).")
        out_dir.mkdir(parents=True, exist_ok=True)
        with self.page.expect_download(timeout=90_000) as dl:
            self.page.locator(ZIP_ICON).first.click()
        target = out_dir / f"{de_number}_AttachedDocument.zip"
        dl.value.save_as(target)
        check_zip_number(target, de_number)
        return target


def acquirer_id(acquirer: str) -> str | None:
    """'NIT 11111111-3\nANA ...' -> '11111111' (identification without check digit)."""
    match = re.search(r"(\d{5,})(?:-\d)?", acquirer or "")
    return match.group(1) if match else None


def dian_rejected(doc: DocRow) -> bool:
    """The DIAN rejected it ("Validaciones contienen errores en campos mandatorios"...)."""
    return bool(re.search(r"error|rechaz", doc.dian, re.IGNORECASE))


def duplicates_in(index: list[DocRow], provider_id: str, total: int) -> list[DocRow]:
    """Same provider (exact ID) and same amount in a document list. Documents the DIAN
    rejected are not valid documents, so they never count as duplicates."""
    found = [d for d in index if acquirer_id(d.acquirer) == provider_id and d.value == total]
    for d in found:
        if dian_rejected(d):
            log.info("%s del proveedor %s fue rechazado por la DIAN; no cuenta como duplicado", d.number, provider_id)
    return [d for d in found if not dian_rejected(d)]


def check_zip_number(zip_path: Path, de_number: str) -> None:
    """The inner file is named like DE25_2264.zip: it must match the captured number."""
    with zipfile.ZipFile(zip_path) as zf:
        found = {de_number_from_filename(n) for n in zf.namelist()} - {None}
    if found and de_number not in found:
        raise DocumentsError(f"El ZIP descargado es de {sorted(found)}, no de {de_number}.")
