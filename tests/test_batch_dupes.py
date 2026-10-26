"""One duplicate search per batch: exact-ID matching, in-batch duplicates and pagination."""
from collections import defaultdict
from datetime import date

import pytest
from playwright.sync_api import sync_playwright

from app.mye_documents import DocRow, MyeDocuments, acquirer_id, duplicates_in

INDEX = [
    DocRow(0, "DE252264", "NIT 11111111-3\nANA MARIA PEREZ LOPEZ", "Procesado", 3250000),
    DocRow(1, "DE252200", "NIT 111111111-1\nOTRO", "Procesado", 3250000),
]


def test_acquirer_id_and_exact_match():
    assert acquirer_id("NIT 11111111-3\nANA") == "11111111"
    assert [d.number for d in duplicates_in(INDEX, "11111111", 3250000)] == ["DE252264"]
    assert duplicates_in(INDEX, "1111111", 3250000) == []  # a prefix is not the same provider
    assert duplicates_in(INDEX, "11111111", 1) == []


def test_document_rejected_by_dian_is_not_a_duplicate():
    index = [DocRow(0, "DE252300", "CC 1044444444\nPEDRO", "Validaciones contienen errores en campos mandatorios.\n"
                    "(1 Observaciones)", 4400000),
             DocRow(1, "DE252301", "NIT 1044444444-1\nPEDRO", "Listo Para Enviar", 4400000)]
    assert [d.number for d in duplicates_in(index, "1044444444", 4400000)] == ["DE252301"]


def test_second_row_of_same_provider_and_value_in_batch_is_blocked(tmp_path):
    from tests.test_live_batch import ROW, FakeDocs, FakeMye, FakeUi, make_batch

    from app.myeinvoice import MyeError
    from app.state import StateStore

    with StateStore(tmp_path / "s.db") as store:
        other = ROW.model_copy(update={"oc": "4600000099"})
        for r in (ROW, other):
            store.ensure_pending(r, "2026-10-23", "CORTE 1")
        batch = make_batch(FakeUi(), FakeMye(), FakeDocs(), auto=True)
        batch.dupe_index = []
        batch.times, batch.row_times, batch._pending_times = defaultdict(list), defaultdict(dict), []
        assert batch.register_stage(store, ROW, tmp_path) == "continue"
        with pytest.raises(MyeError, match="duplicado.*DE252265"):
            batch.register_stage(store, other, tmp_path)


PAGE = """
<select name="tablaDocElectronicos_length"><option>25</option><option>200</option></select>
<table id="tablaDocElectronicos"><tbody id="b"></tbody></table>
<div id="tablaDocElectronicos_info"></div>
<a id="tablaDocElectronicos_next" class="paginate_button next">Siguiente</a>
<script>
const pages = %s, total = %d;
const row = r => `<tr><td></td><td>1</td><td>${r[0]}</td><td>G</td><td>NIT ${r[1]}-1\\nX</td>
  <td>Procesado</td><td>c</td><td>e</td><td>m</td><td>v</td><td>$ ${r[2]}</td></tr>`;
let p = 0;
function show() {
  document.getElementById('b').innerHTML = pages[p].map(row).join('');
  document.getElementById('tablaDocElectronicos_info').textContent = `(x al y) de ${total}`;
  if (p === pages.length - 1) document.getElementById('tablaDocElectronicos_next').classList.add('disabled');
}
document.getElementById('tablaDocElectronicos_next').onclick = () => { p++; show(); };
show();
</script>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def run_search_all(browser, pages, total):
    page = browser.new_page()
    page.set_content(PAGE % (pages, total))
    docs = MyeDocuments(page)
    docs._open_and_filter = lambda *a: None  # the real page is MyE's; here it is already loaded
    docs._wait_idle = lambda *a: page.wait_for_timeout(50)
    try:
        return docs.search_all(date(2026, 8, 24))
    finally:
        page.close()


def test_search_all_reads_every_page(browser):
    pages = [[["DE252001", "111111", "1.000"], ["DE252002", "222222", "2.000"]],
             [["DE252003", "333333", "3.000"]]]
    found = run_search_all(browser, pages, 3)
    assert sorted(d.number for d in found) == ["DE252001", "DE252002", "DE252003"]
    assert duplicates_in(found, "333333", 3000)[0].number == "DE252003"


def test_search_all_incomplete_returns_none(browser):
    pages = [[["DE252001", "111111", "1.000"]]]
    assert run_search_all(browser, pages, 4) is None  # MyE says 4, only 1 readable -> per-row search


def test_total_read_with_selected_rows_suffix(browser):
    # Real MyE text: "(1 al 200) de 334 0 filas seleccionadas" (the total is not at the end).
    pages = [[["DE252001", "111111", "1.000"]]]
    page = browser.new_page()
    page.set_content(PAGE % (pages, 1))
    page.evaluate("document.getElementById('tablaDocElectronicos_info').textContent = '(1 al 1) de 1 0 filas seleccionadas'")
    assert MyeDocuments(page)._total_count() == 1
    page.close()
