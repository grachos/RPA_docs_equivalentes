"""Use cases shared by the desktop UI and the CLI."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app import config
from app.excel_reader import read_orders
from app.models import DocState, InvalidRow, Row
from app.state import StateStore
from app.text_utils import format_observation

log = logging.getLogger("doc_soporte")


@dataclass
class LoadResult:
    consolidado: str
    corte_date: date
    items: list[tuple[Row, DocState]] = field(default_factory=list)
    invalid: list[InvalidRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def mye_observation(self) -> str:
        return format_observation(self.corte_date, config.MYE_OBSERVATION_TEMPLATE, self.consolidado)

    @property
    def cadi_observation(self) -> str:
        return format_observation(self.corte_date, config.CADI_OBSERVATION_TEMPLATE, self.consolidado)


def row_observation(state: DocState, template: str, fallback: str) -> str:
    """Observation of a row from ITS stored consolidado and corte date, so a row resumed on
    another day (or typed with another case) keeps the text it was registered with."""
    try:
        return format_observation(date.fromisoformat(state.corte), template, state.consolidado)
    except (TypeError, ValueError):
        return fallback


def fmt_money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def load_batch(
    excel_path: Path,
    consolidado: str,
    corte_date: date,
    only_row: int | None = None,
    limit: int | None = None,
    state_db: Path = config.STATE_DB,
) -> LoadResult:
    """Read and validate the Excel, then register each valid row as PENDING (idempotent)."""
    # Stored in upper case: "Corte 103" and "CORTE 103" are the same consolidado.
    consolidado = " ".join(consolidado.split()).upper()
    if not consolidado:
        raise ValueError("El nombre del consolidado es obligatorio.")

    rows, invalid, warnings = read_orders(excel_path)
    if only_row is not None:
        rows = [r for r in rows if r.excel_row == only_row]
        if not rows:
            warnings.append(f"La fila {only_row} no existe o no es válida.")
    if limit:
        rows = rows[:limit]

    result = LoadResult(consolidado, corte_date, invalid=invalid, warnings=warnings)
    corte = corte_date.isoformat()
    with StateStore(state_db) as store:
        for row in rows:
            other = next((s for s in store.find_by_oc(row.oc) if s.provider_id != row.provider_id), None)
            if other:
                result.warnings.append(
                    f"Fila {row.excel_row}: la OC {row.oc} ya está registrada para otro "
                    f"proveedor ({other.provider_id}); se omite."
                )
                continue
            state = store.ensure_pending(row, corte, consolidado)
            if state.total != row.total:
                result.warnings.append(
                    f"Fila {row.excel_row}: total en Excel ({fmt_money(row.total)}) difiere del "
                    f"registrado ({fmt_money(state.total)})."
                )
            if state.consolidado and state.consolidado.upper() != consolidado:
                result.warnings.append(
                    f"Fila {row.excel_row}: la OC {row.oc} ya pertenece al consolidado "
                    f"{state.consolidado!r}."
                )
            result.items.append((row, state))
            log.info("consolidado=%s row=%s oc=%s status=%s",
                     consolidado, row.excel_row, row.oc, state.status.value)
    return result
