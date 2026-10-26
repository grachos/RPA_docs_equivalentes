"""Progress model: turns the row status messages into "step k of N" and a batch percentage.

The batch code reports each row with a status text (row_update). This module is the
single place that knows which step each status means, so the UI can draw a progress
bar for the whole process and one for the current record.
"""
from __future__ import annotations

from dataclasses import dataclass, field

REAL_STEPS = ("Registro MyE", "Espera DIAN", "Descarga ZIP", "Factura CADI", "En revisión")
SIM_STEPS = ("Simulación",)

# Status text -> steps already completed (REAL mode).
REAL_DONE = {
    "Procesando…": 0,
    "Registrado": 1,
    "Esperando DIAN": 1,
    "Descargando ZIP": 2,
    "ZIP descargado": 3,
    "Factura CADI…": 3,
    "Creada en CADI": 4,
    "En revisión…": 4,
}
SIM_DONE = {"Procesando…": 0, "Formulario CADI…": 0}
FINAL_OK = {"Terminada", "Simulación OK", "CADI simulación OK"}
FINAL_FAIL = {"Error", "Error CADI", "Verificar en MYE", "Verificar en CADI", "No registrada",
              "CADI pendiente", "Omitida", "Revisar"}


@dataclass
class RowProgress:
    done: int = 0
    final: bool = False
    ok: bool = False


@dataclass
class BatchProgress:
    """Progress of one batch. Rows that end (OK or not) count as complete for the batch."""

    ocs: list[str]
    real: bool
    rows: dict[str, RowProgress] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rows = {oc: RowProgress() for oc in self.ocs}

    @property
    def steps(self) -> tuple[str, ...]:
        return REAL_STEPS if self.real else SIM_STEPS

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    def update(self, oc: str, status: str) -> RowProgress:
        row = self.rows.setdefault(oc, RowProgress())
        if status in FINAL_OK or status in FINAL_FAIL:
            row.final, row.ok = True, status in FINAL_OK
            if row.ok:
                row.done = self.total_steps
        else:
            done = (REAL_DONE if self.real else SIM_DONE).get(status)
            if done is not None:
                row.done = max(row.done, done)
        return row

    def row_text(self, oc: str) -> str:
        row = self.rows.get(oc, RowProgress())
        if row.final:
            return "✔" if row.ok else "✖"
        return f"{row.done}/{self.total_steps}"

    def row_label(self, oc: str, status: str) -> str:
        row = self.rows.get(oc, RowProgress())
        if row.final:
            return f"OC {oc} · {status}"
        step = self.steps[min(row.done, self.total_steps - 1)]
        return f"OC {oc} · paso {row.done + 1}/{self.total_steps}: {step}"

    def row_fraction(self, oc: str) -> float:
        row = self.rows.get(oc, RowProgress())
        return 1.0 if row.final else row.done / self.total_steps

    @property
    def fraction(self) -> float:
        if not self.rows:
            return 0.0
        return sum(1.0 if r.final else r.done / self.total_steps for r in self.rows.values()) / len(self.rows)

    def summary(self) -> str:
        finished = sum(r.final for r in self.rows.values())
        ok = sum(r.final and r.ok for r in self.rows.values())
        return (f"{self.fraction:.0%} · {finished} de {len(self.rows)} filas finalizadas "
                f"({ok} OK, {finished - ok} con error u omitidas)")
