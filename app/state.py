"""SQLite state per (OC, provider ID) with a strict forward-only state machine.

PENDING -> MYE_REGISTERED -> DIAN_OK -> ZIP_DOWNLOADED -> CADI_CREATED
        -> CADI_REVIEWED -> DONE, or FAILED(reason) from any step.
A failed row keeps `last_ok_status`, so processing resumes from there.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.models import DocState, DocStatus, Row

STATUS_ORDER: tuple[DocStatus, ...] = (
    DocStatus.PENDING,
    DocStatus.MYE_REGISTERED,
    DocStatus.DIAN_OK,
    DocStatus.ZIP_DOWNLOADED,
    DocStatus.CADI_CREATED,
    DocStatus.CADI_REVIEWED,
    DocStatus.DONE,
)

UPDATABLE_FIELDS = frozenset({"de_number", "dian_status", "cadi_id", "pdf_path", "xml_path"})


class InvalidTransition(Exception):
    pass


def next_status(current: DocStatus) -> DocStatus | None:
    """Next state after `current` (a successful one), or None when DONE."""
    if current == DocStatus.FAILED:
        raise InvalidTransition("FAILED has no next state; use last_ok_status")
    idx = STATUS_ORDER.index(current)
    return STATUS_ORDER[idx + 1] if idx + 1 < len(STATUS_ORDER) else None


def check_transition(last_ok: DocStatus, new: DocStatus) -> None:
    """Only the immediate next state is allowed (no skipping, no going back)."""
    if new == DocStatus.FAILED:
        return
    if next_status(last_ok) != new:
        raise InvalidTransition(f"{last_ok.value} -> {new.value} no permitido")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    oc TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    corte TEXT NOT NULL,
    consolidado TEXT NOT NULL DEFAULT '',
    provider_name TEXT NOT NULL,
    total INTEGER NOT NULL,
    status TEXT NOT NULL,
    last_ok_status TEXT NOT NULL,
    de_number TEXT,
    dian_status TEXT,
    cadi_id TEXT,
    pdf_path TEXT,
    xml_path TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (oc, provider_id)
);
CREATE INDEX IF NOT EXISTS idx_docs_corte ON docs(corte);
CREATE INDEX IF NOT EXISTS idx_docs_de ON docs(de_number);
"""

# Columns added after the first release: (name, DDL), applied if missing.
_MIGRATIONS = (
    ("consolidado", "ALTER TABLE docs ADD COLUMN consolidado TEXT NOT NULL DEFAULT ''"),
    ("timings", "ALTER TABLE docs ADD COLUMN timings TEXT"),
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(docs)")}
        with self.conn:
            for name, ddl in _MIGRATIONS:
                if name not in columns:
                    self.conn.execute(ddl)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def _to_state(r: sqlite3.Row) -> DocState:
        return DocState(**dict(r))

    def get(self, oc: str, provider_id: str) -> DocState | None:
        r = self.conn.execute(
            "SELECT * FROM docs WHERE oc = ? AND provider_id = ?", (oc, provider_id)
        ).fetchone()
        return self._to_state(r) if r else None

    def find_by_oc(self, oc: str) -> list[DocState]:
        rows = self.conn.execute("SELECT * FROM docs WHERE oc = ?", (oc,)).fetchall()
        return [self._to_state(r) for r in rows]

    def find_by_de(self, de_number: str) -> DocState | None:
        r = self.conn.execute("SELECT * FROM docs WHERE de_number = ?", (de_number,)).fetchone()
        return self._to_state(r) if r else None

    def ensure_pending(self, row: Row, corte: str, consolidado: str) -> DocState:
        """Insert the row as PENDING if unknown; otherwise return the existing state.

        A row of the same consolidado not registered in MyE yet (still PENDING, no DE) takes
        the corte date typed now (its observation is built from it); a registered row keeps
        the one it was registered with. Nothing else of an existing row is changed.
        """
        existing = self.get(row.oc, row.provider_id)
        retyped = (existing and existing.consolidado == consolidado and existing.corte != corte
                   and existing.last_ok_status == DocStatus.PENDING and not existing.de_number)
        if existing and consolidado and (retyped or not existing.consolidado):
            with self.conn:
                self.conn.execute(
                    "UPDATE docs SET consolidado = ?, corte = ? WHERE oc = ? AND provider_id = ?",
                    (consolidado, corte, row.oc, row.provider_id))
            existing = self.get(row.oc, row.provider_id)
        if existing:
            return existing
        with self.conn:
            self.conn.execute(
                "INSERT INTO docs (oc, provider_id, corte, consolidado, provider_name, total, "
                "status, last_ok_status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row.oc, row.provider_id, corte, consolidado, row.provider_name, row.total,
                 DocStatus.PENDING.value, DocStatus.PENDING.value, _now()),
            )
        return self.get(row.oc, row.provider_id)  # type: ignore[return-value]

    def advance(self, oc: str, provider_id: str, new: DocStatus, **fields: str) -> DocState:
        unknown = set(fields) - UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Campos no permitidos: {sorted(unknown)}")
        current = self.get(oc, provider_id)
        if current is None:
            raise KeyError(f"OC {oc} / {provider_id} no existe en el estado")
        check_transition(current.last_ok_status, new)
        assignments = ", ".join(f"{k} = ?" for k in fields)
        sql = (
            "UPDATE docs SET status = ?, last_ok_status = ?, error = NULL, updated_at = ?"
            + (f", {assignments}" if assignments else "")
            + " WHERE oc = ? AND provider_id = ?"
        )
        with self.conn:
            self.conn.execute(sql, (new.value, new.value, _now(), *fields.values(), oc, provider_id))
        return self.get(oc, provider_id)  # type: ignore[return-value]

    def record_timing(self, oc: str, provider_id: str, process: str, seconds: float) -> None:
        """Store how long a process took for this row (a retry overwrites that process)."""
        current = self.get(oc, provider_id)
        if current is None:
            return
        data = json.loads(current.timings or "{}")
        data[process] = round(seconds, 1)
        with self.conn:
            self.conn.execute("UPDATE docs SET timings = ? WHERE oc = ? AND provider_id = ?",
                              (json.dumps(data, ensure_ascii=False), oc, provider_id))

    def mark_failed(self, oc: str, provider_id: str, reason: str) -> DocState:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE docs SET status = ?, error = ?, updated_at = ? WHERE oc = ? AND provider_id = ?",
                (DocStatus.FAILED.value, reason[:1000], _now(), oc, provider_id),
            )
        if cur.rowcount == 0:
            raise KeyError(f"OC {oc} / {provider_id} no existe en el estado")
        return self.get(oc, provider_id)  # type: ignore[return-value]

    def consolidados(self) -> list[str]:
        """Distinct consolidado names, most recent first."""
        rows = self.conn.execute(
            "SELECT consolidado FROM docs WHERE consolidado <> '' "
            "GROUP BY consolidado COLLATE NOCASE ORDER BY MAX(updated_at) DESC"
        ).fetchall()
        return [r["consolidado"] for r in rows]

    def list(
        self,
        corte: str | None = None,
        status: DocStatus | None = None,
        consolidado: str | None = None,
    ) -> list[DocState]:
        sql, params = "SELECT * FROM docs WHERE 1=1", []
        if corte:
            sql += " AND corte = ?"
            params.append(corte)
        if consolidado:
            sql += " AND consolidado = ? COLLATE NOCASE"
            params.append(consolidado)
        if status:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY corte, updated_at"
        return [self._to_state(r) for r in self.conn.execute(sql, params).fetchall()]
