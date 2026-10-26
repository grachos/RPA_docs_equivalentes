"""DIAN wait: only polls (the send is MyE's monitor), time-based limit."""
from datetime import date

import pytest

from app import config
from app.mye_documents import DocRow, DocumentsError, MyeDocuments


class FakePage:
    def wait_for_timeout(self, ms):
        pass


def docs_with(statuses):
    d = MyeDocuments(FakePage())
    seq = iter(statuses)
    d.find = lambda de, pid, since: DocRow(0, de, "NIT 1", next(seq), 1)
    return d


def test_waits_through_listo_para_enviar():
    d = docs_with(["Listo Para Enviar.", "Listo Para Enviar.", "Procesado Correctamente.\n(1 Obs)"])
    assert d.wait_dian_ok("DE252267", "1", date(2026, 10, 1)) == "Procesado Correctamente."


def test_rejection_stops():
    with pytest.raises(DocumentsError, match="no aceptó"):
        docs_with(["Rechazado por la DIAN"]).wait_dian_ok("DE1234567", "1", date(2026, 10, 1))


def test_time_limit(monkeypatch):
    monkeypatch.setattr(config, "DIAN_MAX_WAIT_SECONDS", 0)
    with pytest.raises(DocumentsError, match="no se registra de nuevo"):
        docs_with([]).wait_dian_ok("DE1234567", "1", date(2026, 10, 1))
