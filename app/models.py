"""Data models shared across modules."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Row(BaseModel):
    """A validated Excel row: one row = one documento soporte."""

    excel_row: int
    provider_id: str
    provider_name: str
    oc: str
    total: int = Field(gt=0)


class InvalidRow(BaseModel):
    excel_row: int
    errors: list[str]
    raw: dict[str, str]


class DocStatus(str, Enum):
    PENDING = "PENDING"
    MYE_REGISTERED = "MYE_REGISTERED"
    DIAN_OK = "DIAN_OK"
    ZIP_DOWNLOADED = "ZIP_DOWNLOADED"
    CADI_CREATED = "CADI_CREATED"
    CADI_REVIEWED = "CADI_REVIEWED"
    DONE = "DONE"
    FAILED = "FAILED"


class DocState(BaseModel):
    """Persisted progress of one (OC, provider) pair."""

    oc: str
    provider_id: str
    corte: str  # corte date, ISO format
    consolidado: str = ""
    provider_name: str
    total: int
    status: DocStatus
    last_ok_status: DocStatus
    de_number: str | None = None
    dian_status: str | None = None
    cadi_id: str | None = None
    pdf_path: str | None = None
    xml_path: str | None = None
    error: str | None = None
    timings: str | None = None  # JSON {process: seconds} of the last run of each process
    updated_at: str = ""
