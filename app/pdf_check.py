"""Validate the text of a documento soporte PDF (preview or final) against the Excel row."""
from __future__ import annotations

import io
import re
from datetime import date

from pypdf import PdfReader

from app import config
from app.models import Row
from app.text_utils import normalize_text


def pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pdf_issue_date(text: str) -> date | None:
    """Issue date printed under "Fecha de Operación" (format 2026/10/23)."""
    found = {m for m in re.findall(r"\b(\d{4})/(\d{2})/(\d{2})\b", text)}
    if len(found) != 1:
        return None
    y, m, d = (int(x) for x in found.pop())
    return date(y, m, d)


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def check_pdf_text(text: str, row: Row, observation: str, de_number: str | None = None,
                   acquirer_name: str | None = None, acquirer_surnames: str | None = None) -> list[str]:
    """Return the list of mismatches (empty = OK).

    The provider is identified by its ID (user rule: the Excel name may be incomplete).
    The printed name is checked against the name MyE has for that ID (`acquirer_name`,
    already corrected) when known, and the surnames must not appear twice.
    """
    norm = normalize_text(text)
    compact = norm.replace(" ", "")
    problems: list[str] = []

    if acquirer_name:
        for token in normalize_text(acquirer_name).split():
            if token not in norm:
                problems.append(f"nombre: falta {token!r} del proveedor en MyE")
                break
    # MyE's 'Nombres' bug shows up as the surnames printed twice.
    surname = normalize_text(acquirer_surnames).split() if acquirer_surnames else []
    if not surname:
        excel = normalize_text(row.provider_name).split()
        surname = excel[-2:] if len(excel) >= 3 else excel[-1:]
    if surname and " ".join(surname + surname) in norm:
        problems.append(f"apellido duplicado en el nombre ({' '.join(surname)})")
    if row.provider_id not in _digits_only(text) and row.provider_id not in norm:
        problems.append(f"ID {row.provider_id} no aparece")
    if normalize_text(config.MYE_CONCEPT_DESCRIPTION) not in norm:
        problems.append("descripción del concepto no aparece")
    # Totals may be printed as 1.770.000,00 / 1,770,000.00 / 1770000.
    amounts = {re.sub(r"[.,]", "", m)[:-2] if re.search(r"[.,]\d{2}$", m) else re.sub(r"[.,]", "", m)
               for m in re.findall(r"\d[\d.,]*\d", text)}
    if str(row.total) not in amounts:
        problems.append(f"total {row.total} no aparece")
    if normalize_text(observation).replace(" ", "") not in compact:
        problems.append(f"observación {observation!r} no aparece")
    if row.oc not in text:
        problems.append(f"OC {row.oc} no aparece")
    if de_number:
        alt = f"{de_number[:4]}_{de_number[4:]}"  # the PDF may print DE25_2264
        if de_number not in compact and normalize_text(alt) not in norm:
            problems.append(f"número {de_number} no aparece")
    return problems
