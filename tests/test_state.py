import pytest

from app.models import DocStatus, Row
from app.state import InvalidTransition, StateStore, check_transition, next_status

ROW = Row(excel_row=2, provider_id="11111111", provider_name="ANA", oc="4600485727", total=3250000)


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "state.db") as s:
        yield s


def test_next_status_chain():
    assert next_status(DocStatus.PENDING) == DocStatus.MYE_REGISTERED
    assert next_status(DocStatus.CADI_REVIEWED) == DocStatus.DONE
    assert next_status(DocStatus.DONE) is None
    with pytest.raises(InvalidTransition):
        next_status(DocStatus.FAILED)


def test_no_skipping_or_going_back():
    with pytest.raises(InvalidTransition):
        check_transition(DocStatus.PENDING, DocStatus.DIAN_OK)
    with pytest.raises(InvalidTransition):
        check_transition(DocStatus.DIAN_OK, DocStatus.MYE_REGISTERED)
    check_transition(DocStatus.ZIP_DOWNLOADED, DocStatus.FAILED)


def test_ensure_pending_is_idempotent(store):
    first = store.ensure_pending(ROW, "2026-10-09", "CORTE 103")
    store.advance(ROW.oc, ROW.provider_id, DocStatus.MYE_REGISTERED, de_number="DE252264")
    again = store.ensure_pending(ROW, "2026-10-09", "CORTE 103")
    assert first.status == DocStatus.PENDING
    assert again.status == DocStatus.MYE_REGISTERED and again.de_number == "DE252264"
    assert store.find_by_de("DE252264").oc == ROW.oc


def test_fail_and_resume_from_last_ok(store):
    store.ensure_pending(ROW, "2026-10-09", "CORTE 103")
    for st in (DocStatus.MYE_REGISTERED, DocStatus.DIAN_OK, DocStatus.ZIP_DOWNLOADED,
               DocStatus.CADI_CREATED):
        store.advance(ROW.oc, ROW.provider_id, st)
    failed = store.mark_failed(ROW.oc, ROW.provider_id, "Revisado por falló")
    assert failed.status == DocStatus.FAILED
    assert failed.last_ok_status == DocStatus.CADI_CREATED
    assert store.list(status=DocStatus.FAILED)[0].error == "Revisado por falló"

    resumed = store.advance(ROW.oc, ROW.provider_id, DocStatus.CADI_REVIEWED, cadi_id="234004")
    assert resumed.status == DocStatus.CADI_REVIEWED and resumed.error is None


def test_advance_rejects_unknown_fields(store):
    store.ensure_pending(ROW, "2026-10-09", "CORTE 103")
    with pytest.raises(ValueError):
        store.advance(ROW.oc, ROW.provider_id, DocStatus.MYE_REGISTERED, status="DONE")


def test_ensure_pending_backfills_missing_consolidado(store):
    store.ensure_pending(ROW, "2026-10-09", "")
    store.advance(ROW.oc, ROW.provider_id, DocStatus.MYE_REGISTERED, de_number="DE252265")
    st = store.ensure_pending(ROW, "2026-10-23", "CORTE 103")
    assert (st.consolidado, st.corte, st.de_number) == ("CORTE 103", "2026-10-23", "DE252265")
    again = store.ensure_pending(ROW, "2026-10-30", "CORTE 104")  # never overwritten once set
    assert (again.consolidado, again.corte) == ("CORTE 103", "2026-10-23")


def test_consolidados_and_filter(store):
    store.ensure_pending(ROW, "2026-10-09", "CORTE 103")
    other = ROW.model_copy(update={"oc": "4600000001"})
    store.ensure_pending(other, "2026-10-16", "CORTE 104")
    assert set(store.consolidados()) == {"CORTE 103", "CORTE 104"}
    assert [s.oc for s in store.list(consolidado="CORTE 104")] == ["4600000001"]


def test_migration_adds_consolidado(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE docs (oc TEXT NOT NULL, provider_id TEXT NOT NULL, corte TEXT NOT NULL, "
                 "provider_name TEXT NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL, "
                 "last_ok_status TEXT NOT NULL, de_number TEXT, dian_status TEXT, cadi_id TEXT, "
                 "pdf_path TEXT, xml_path TEXT, error TEXT, updated_at TEXT NOT NULL, "
                 "PRIMARY KEY (oc, provider_id))")
    conn.execute("INSERT INTO docs VALUES ('4600485726','33333333','2026-10-09','ANA',1770000,"
                 "'PENDING','PENDING',NULL,NULL,NULL,NULL,NULL,NULL,'2026-10-22T17:20:00')")
    conn.commit(); conn.close()
    with StateStore(db) as s:
        assert s.get("4600485726", "33333333").consolidado == ""


def test_unregistered_row_takes_the_new_corte_registered_row_keeps_it(store):
    store.ensure_pending(ROW, "2026-10-23", "CORTE 103")
    retried = store.ensure_pending(ROW, "2026-10-09", "CORTE 103")  # never registered in MyE
    assert retried.corte == "2026-10-09"
    store.advance(ROW.oc, ROW.provider_id, DocStatus.MYE_REGISTERED, de_number="DE252300")
    again = store.ensure_pending(ROW, "2026-10-30", "CORTE 103")
    assert again.corte == "2026-10-09"
