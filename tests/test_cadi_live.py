"""Phase-5 CADI stage with a fake portal (no browser)."""
from pathlib import Path

import pytest

from app import cadi_live
from app.cadi import CadiError, CadiInvoice
from app.models import DocStatus, Row
from app.state import StateStore

ROW = Row(excel_row=2, provider_id="33333333", provider_name="ANA ROSA DIAZ MORA",
          oc="4600485726", total=1770000)
OBS = "CORTE 103 23 octubre 2026"


class FakeCadi:
    def __init__(self, existing=(), status="Abierto"):
        self.existing, self.status = list(existing), status
        self.created = self.reviewed = 0

    def find_invoices(self, number):
        return self.existing

    def fill_create_form(self, form):
        self.form = form

    def submit_create(self, form):
        self.created += 1
        return "234500"

    def open_details(self, invoice_id):
        return {"ID": invoice_id, "Estado": self.status}

    def fill_review(self, invoice_id, oc, observation):
        assert (oc, observation) == (ROW.oc, OBS)

    def submit_review(self, invoice_id, oc, total):
        self.reviewed += 1
        self.status = "En revisión"

    def verify_reviewed(self, invoice_id, oc, total):
        return {}


class FakeUi:
    def __init__(self, answer=True):
        self.answer, self.asked = answer, []

    def log(self, m):
        pass

    def row_update(self, *a):
        pass

    def confirm(self, text, pdf, label):
        self.asked.append(label)
        assert "DE252265" in text and "OPERATIVO" in text and OBS in text
        return self.answer


@pytest.fixture
def store(tmp_path, monkeypatch):
    pdf = tmp_path / "DE252265.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setattr(cadi_live, "pdf_text", lambda b: "Fecha de Operación\n2026/10/23\n")
    with StateStore(tmp_path / "s.db") as s:
        s.ensure_pending(ROW, "2026-10-23", "CORTE 103")
        for st in (DocStatus.MYE_REGISTERED, DocStatus.DIAN_OK):
            s.advance(ROW.oc, ROW.provider_id, st, de_number="DE252265")
        s.advance(ROW.oc, ROW.provider_id, DocStatus.ZIP_DOWNLOADED, pdf_path=str(pdf))
        yield s


def stage(fake, ui, auto=False):
    st = cadi_live.CadiStage(lambda: None, ui, OBS, auto)
    st._cadi = fake
    return st


def current(store):
    return store.get(ROW.oc, ROW.provider_id)


def test_create_and_review_reaches_done(store):
    fake, ui = FakeCadi(), FakeUi()
    final = stage(fake, ui).run(store, ROW, current(store))
    assert final.status == DocStatus.DONE and final.cadi_id == "234500"
    assert (fake.created, fake.reviewed) == (1, 1)
    assert ui.asked == [cadi_live.APPROVE_LABEL]
    assert fake.form.issue_date.isoformat() == "2026-10-23"


def test_user_declines_creates_nothing(store):
    fake = FakeCadi()
    assert stage(fake, FakeUi(answer=False)).run(store, ROW, current(store)) is None
    assert fake.created == 0 and current(store).status == DocStatus.ZIP_DOWNLOADED


def test_existing_invoice_is_adopted_not_duplicated(store):
    inv = CadiInvoice("234004", "DE252265", "FAC ELEC - TERCERIZACION", ROW.provider_id, "ANA",
                      ROW.oc, "En revisión", "1,770,000.00")
    fake, ui = FakeCadi([inv], status="En revisión"), FakeUi()
    final = stage(fake, ui).run(store, ROW, current(store))
    assert final.status == DocStatus.DONE and final.cadi_id == "234004"
    assert fake.created == 0 and fake.reviewed == 0 and ui.asked == []


def test_existing_with_other_nit_stops(store):
    inv = CadiInvoice("999", "DE252265", "X", "11111111", "OTRO", "", "Abierto", "1")
    with pytest.raises(CadiError, match="otro NIT"):
        stage(FakeCadi([inv]), FakeUi()).run(store, ROW, current(store))


def test_resume_from_created_only_reviews(store):
    store.advance(ROW.oc, ROW.provider_id, DocStatus.CADI_CREATED, cadi_id="234500")
    fake, ui = FakeCadi(), FakeUi()
    final = stage(fake, ui).run(store, ROW, current(store))
    assert final.status == DocStatus.DONE and fake.created == 0 and fake.reviewed == 1 and ui.asked == []


def test_wrong_cadi_state_stops(store):
    store.advance(ROW.oc, ROW.provider_id, DocStatus.CADI_CREATED, cadi_id="234500")
    with pytest.raises(CadiError, match="Anulado"):
        stage(FakeCadi(status="Anulado"), FakeUi()).run(store, ROW, current(store))


def test_auto_confirm_does_not_ask(store):
    ui = FakeUi()
    stage(FakeCadi(), ui, auto=True).run(store, ROW, current(store))
    assert ui.asked == []
