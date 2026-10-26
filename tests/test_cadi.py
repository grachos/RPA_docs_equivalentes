"""CADI phase-4 logic with a fake portal (no browser)."""
import threading
from datetime import date
from pathlib import Path

import pytest

from app import cadi_batch
from app.cadi import CadiError, CadiInvoice, build_form
from app.models import DocState, DocStatus, Row
from app.pdf_check import pdf_issue_date

ROW = Row(excel_row=2, provider_id="33333333", provider_name="ANA ROSA DIAZ MORA",
          oc="4600485726", total=1770000)


def state(pdf: Path | None) -> DocState:
    return DocState(oc=ROW.oc, provider_id=ROW.provider_id, corte="2026-10-23", consolidado="CORTE 103",
                    provider_name=ROW.provider_name, total=ROW.total, status=DocStatus.ZIP_DOWNLOADED,
                    last_ok_status=DocStatus.ZIP_DOWNLOADED, de_number="DE252265",
                    pdf_path=str(pdf) if pdf else None)


class FakeCadi:
    def __init__(self, existing=()):
        self.existing, self.forms = list(existing), []

    def ensure_logged_in(self, notify):
        pass

    def find_invoices(self, number):
        return self.existing

    def fill_create_form(self, form):
        self.forms.append(form)

    def screenshot_form(self, path):
        return path


class FakeUi:
    def __init__(self):
        self.updates = []

    def log(self, m):
        pass

    def row_update(self, oc, status, detail, tag):
        self.updates.append((status, detail))


@pytest.fixture
def pdf(tmp_path, monkeypatch):
    path = tmp_path / "DE252265.pdf"
    path.write_bytes(b"%PDF")
    monkeypatch.setattr(cadi_batch, "pdf_text", lambda b: "Fecha de Operación\n4600485726\n2026/10/23\n")
    return path


def run(fake, st):
    ui = FakeUi()
    original = cadi_batch.Cadi
    cadi_batch.Cadi = lambda page: fake  # the module builds Cadi(page)
    try:
        result = cadi_batch.cadi_simulation(None, [(ROW, st)], ui, threading.Event())
    finally:
        cadi_batch.Cadi = original
    return result, ui


def test_fills_form_with_pdf_date(pdf):
    fake = FakeCadi()
    (ok, failed), ui = run(fake, state(pdf))
    assert (ok, failed) == (1, 0)
    form = fake.forms[0]
    assert (form.invoice_number, form.supplier_nit, form.order_reference, form.total) == \
        ("DE252265", "33333333", "4600485726", 1770000)
    assert form.issue_date == date(2026, 10, 23) and form.pdf == pdf
    assert ui.updates[-1][0] == "CADI simulación OK"


def test_existing_invoice_blocks(pdf):
    fake = FakeCadi([CadiInvoice("234010", "DE252265", "FAC ELEC - TERCERIZACION", "33333333",
                                 "ANA", "4600485726", "Abierto", "1,770,000.00")])
    (ok, failed), ui = run(fake, state(pdf))
    assert (ok, failed) == (0, 1) and not fake.forms
    assert "Ya existe en CADI" in ui.updates[-1][1]


def test_missing_pdf_blocks():
    fake = FakeCadi()
    (ok, failed), ui = run(fake, state(None))
    assert failed == 1 and not fake.forms


def test_build_form_rules(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    with pytest.raises(CadiError):
        build_form(ROW, "DE" + "9" * 20, date(2026, 10, 23), pdf)
    with pytest.raises(CadiError):
        build_form(ROW, "DE252265", date(2026, 10, 23), tmp_path / "missing.pdf")


def test_pdf_issue_date():
    assert pdf_issue_date("Fecha de Operación\n2026/10/22\n") == date(2026, 10, 22)
    assert pdf_issue_date("sin fecha") is None
    assert pdf_issue_date("2026/10/22 y 2026/10/23") is None  # ambiguous
