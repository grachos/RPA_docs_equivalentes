"""Final report (CSV + XLSX) built from the SQLite state."""
from __future__ import annotations

import csv
import json
import re
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from app.models import DocState

COLUMNS = (
    ("Consolidado", "consolidado"),
    ("Fecha corte", "corte"),
    ("Proveedor", "provider_name"),
    ("ID", "provider_id"),
    ("OC", "oc"),
    ("Total", "total"),
    ("Número DE", "de_number"),
    ("Estado DIAN", "dian_status"),
    ("ID CADI", "cadi_id"),
    ("Estado final", "status"),
    ("Error", "error"),
)


# Per-row process times (seconds), stored by the REAL batch in DocState.timings.
PROCESSES = ("Registro MyE", "Espera DIAN", "Descarga ZIP", "CADI")
TIME_HEADERS = tuple(f"{p} (s)" for p in PROCESSES) + ("Total fila (s)",)


def row_times(state: DocState) -> list:
    data = json.loads(state.timings or "{}")
    values = [data.get(p) for p in PROCESSES]
    known = [v for v in values if v is not None]
    return values + [round(sum(known), 1) if known else None]


def _values(state: DocState) -> list:
    out = []
    for _, attr in COLUMNS:
        value = getattr(state, attr)
        out.append(value.value if hasattr(value, "value") else value)
    return out + row_times(state)


def write_report(states: list[DocState], out_dir: Path, label: str,
                 summary: list[tuple[str, object]] | None = None) -> tuple[Path, Path]:
    """CSV + XLSX. `summary` (batch totals) goes to a "Resumen" sheet of the XLSX."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^\w-]+", "_", label).strip("_") or "todos"
    stem = f"report_{safe_label}_{date.today().isoformat()}"
    csv_path, xlsx_path = out_dir / f"{stem}.csv", out_dir / f"{stem}.xlsx"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([h for h, _ in COLUMNS] + list(TIME_HEADERS))
        writer.writerows(_values(s) for s in states)

    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"
    ws.append([h for h, _ in COLUMNS] + list(TIME_HEADERS))
    for s in states:
        ws.append(_values(s))
    if summary:
        sheet = wb.create_sheet("Resumen")
        sheet.append(["Concepto", "Valor"])
        for key, value in summary:
            sheet.append([key, value])
        sheet.column_dimensions["A"].width = 40
        sheet.column_dimensions["B"].width = 30
    wb.save(xlsx_path)
    return csv_path, xlsx_path
