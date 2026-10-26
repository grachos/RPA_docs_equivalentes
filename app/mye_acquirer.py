"""MyE-invoice acquirer (provider) helpers: pick it from the suggestions, read/set fields.

The address selects of MyE use DANE codes as values (confirmed live 2026-10-26):
país "CO" -> departamento = first 2 DANE digits ("11", "05") -> ciudad = first 5 ("11001",
"05360"). Options are chosen by code, never by name ("Bogotá D.C." vs "Bogotá").
See app/mye_tercero.py for applying Silogtran's data.
"""
from __future__ import annotations

import logging
import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout


log = logging.getLogger("doc_soporte")

SEL_PAIS = "#paisAdquiriente"
SEL_DEPTO = "#departamentosAdquiriente"
SEL_CIUDAD = "#ciudadesAdquiriente"
EMPTY_SELECT = ("", "-1", "0")


class ProviderNotFound(Exception):
    """No suggestion with this exact ID: the provider does not exist in MyE."""


def pick_provider_suggestion(page: Page, field, provider_id: str, attempts: int = 2,
                             prefer: str | None = None) -> None:
    """Click the typeahead suggestion whose ID is EXACTLY provider_id ("NIT <id> - NAME").

    The list (ul.typeahead) can close before it is read even though the provider exists
    (seen live with 22222222), so: retype up to `attempts` times and reopen a hidden list.
    Only when no attempt shows that exact ID is the provider considered missing.
    The same ID can appear twice ("NIT 22222222" and "CC 22222222": MyE saves the acquirer by
    document type at preview time); `prefer` (e.g. "CC", from Silogtran) picks one of them.
    """
    exact = re.compile(rf"^\s*\S+\s+{re.escape(provider_id)}\s+-\s")
    items = page.locator("ul.typeahead li a").filter(has_text=exact)
    for attempt in range(1, attempts + 1):
        field.fill("")
        field.press_sequentially(provider_id, delay=80)
        # ~12 s for the first lookup, ~7 s for the retry (a missing provider cost ~66 s before)
        for _ in range(24 if attempt == 1 else 14):
            if items.count():
                break
            page.wait_for_timeout(500)
        if not items.count():
            log.info("Proveedor %s: sin sugerencia (intento %s/%s)", provider_id, attempt, attempts)
            continue
        chosen = items
        if items.count() > 1 and prefer:
            chosen = items.filter(has_text=re.compile(rf"^\s*{re.escape(prefer)}\s+{re.escape(provider_id)}\s+-\s"))
        if chosen.count() != 1:
            raise ValueError(f"Varias sugerencias con el ID {provider_id}; revise manualmente.")
        items = chosen
        if not items.first.is_visible():  # list closed too early: reopen it
            field.press("Space")
            field.press("Backspace")
            try:
                items.first.wait_for(state="visible", timeout=5_000)
            except PWTimeout:
                log.info("Proveedor %s: sugerencia oculta (intento %s/%s)", provider_id, attempt, attempts)
                continue
        items.first.click()
        return
    raise ProviderNotFound(f"El proveedor {provider_id} no existe en MyE-invoice (no apareció su ID en "
                           f"{attempts} intentos). No se inventan datos.")


def _value(page: Page, selector: str) -> str:
    return page.eval_on_selector(selector, "e => (e.value || '').trim()")


def _set(page: Page, selector: str, value: str) -> None:
    """Set a value WITHOUT keystrokes (several of these inputs have an autocomplete)."""
    page.evaluate("([s, v]) => { $(s).first().val(v).trigger('change'); $('.ui-autocomplete, ul.typeahead').hide(); }",
                  [selector, value])


def _wait_option(page: Page, selector: str, value: str, timeout_ms: int = 15_000) -> bool:
    for _ in range(timeout_ms // 300):
        if page.evaluate("([s, v]) => [...document.querySelectorAll(s + ' option')].some(o => o.value === v)",
                         [selector, value]):
            return True
        page.wait_for_timeout(300)
    return False


def missing_address(page: Page, required: dict[str, str], wait_rounds: int = 20) -> list[str]:
    """Labels of required address fields still empty. Address data arrives in cascade
    (país -> departamento -> ciudad), so give it time before concluding it is missing."""
    missing: list[str] = []
    for _ in range(wait_rounds):
        missing = [label for label, sel in required.items() if _value(page, sel) in EMPTY_SELECT]
        if not missing:
            break
        page.wait_for_timeout(500)
    return missing
