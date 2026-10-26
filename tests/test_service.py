from datetime import date

import pytest

from app.excel_reader import TEMPLATE_HEADERS
from app.service import load_batch
from openpyxl import Workbook


@pytest.fixture
def excel(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(list(TEMPLATE_HEADERS))
    ws.append([33333333, "ANA ROSA DIAZ MORA", 4600485726, 1770000])
    path = tmp_path / "ordenes.xlsx"
    wb.save(path)
    return path


def test_load_batch_registers_pending(excel, tmp_path):
    db = tmp_path / "s.db"
    result = load_batch(excel, "  CORTE   103 ", date(2026, 10, 9), state_db=db)
    assert result.consolidado == "CORTE 103"
    assert [(r.oc, st.status.value, st.consolidado) for r, st in result.items] == [
        ("4600485726", "PENDING", "CORTE 103")]
    assert result.mye_observation == "CORTE 103 09 octubre 2026"
    assert result.cadi_observation == "CORTE 103 09 octubre 2026"

    again = load_batch(excel, "CORTE 104", date(2026, 10, 9), state_db=db)
    assert any("ya pertenece al consolidado" in w for w in again.warnings)


def test_consolidado_required(excel, tmp_path):
    with pytest.raises(ValueError):
        load_batch(excel, "  ", date(2026, 10, 9), state_db=tmp_path / "s.db")


def test_row_observation_uses_the_row_own_corte_and_consolidado():
    from app.models import DocState, DocStatus
    from app.service import row_observation

    st = DocState(oc="1", provider_id="2", corte="2026-10-23", consolidado="CORTE 103", provider_name="X",
                  total=1, status=DocStatus.MYE_REGISTERED, last_ok_status=DocStatus.MYE_REGISTERED)
    # Resumed on another day with another consolidado typed: the stored values win.
    assert row_observation(st, "{consolidado} {dd} {month} {yyyy}", "otra") == "CORTE 103 23 octubre 2026"
    assert row_observation(st.model_copy(update={"corte": ""}), "{dd}", "otra") == "otra"


def test_consolidado_is_case_insensitive(excel, tmp_path):
    from app.state import StateStore

    db = tmp_path / "s.db"
    first = load_batch(excel, "Corte 103", date(2026, 10, 23), state_db=db)
    assert first.consolidado == "CORTE 103"
    again = load_batch(excel, "corte 103", date(2026, 10, 23), state_db=db)
    assert not any("ya pertenece" in w for w in again.warnings)
    with StateStore(db) as s:
        assert len(s.list(consolidado="Corte 103")) == 1 and s.consolidados() == ["CORTE 103"]
