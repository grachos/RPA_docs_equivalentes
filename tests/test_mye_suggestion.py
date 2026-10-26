"""Provider typeahead selection against a minimal page that mimics MyE (headless browser)."""
import pytest
from playwright.sync_api import sync_playwright

from app.mye_acquirer import ProviderNotFound, pick_provider_suggestion

PAGE = """
<input id="identificacionAdquiriente" autocomplete="off">
<ul class="typeahead dropdown-menu" style="display:none">%s</ul>
<div id="picked"></div>
<script>
  const ul = document.querySelector('ul.typeahead');
  // Like MyE: the list exists but may be hidden; any keyup shows it again.
  document.getElementById('identificacionAdquiriente').addEventListener('keyup', () => {
    if (%s) ul.style.display = 'block'; });
  ul.addEventListener('click', e => { document.getElementById('picked').textContent = e.target.textContent; });
</script>
"""
ITEM = '<li><a class="dropdown-item" href="#">NIT <strong>%s</strong> - %s</a></li>'


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def pick(browser, items: list[tuple[str, str]], provider_id: str, shows_on_keyup: bool = True):
    page = browser.new_page()
    page.set_content(PAGE % ("".join(ITEM % i for i in items), "true" if shows_on_keyup else "false"))
    try:
        pick_provider_suggestion(page, page.locator("#identificacionAdquiriente"), provider_id, attempts=2)
        return page.inner_text("#picked")
    finally:
        page.close()


def test_hidden_list_is_reopened_and_exact_id_chosen(browser):
    picked = pick(browser, [("222222221", "OTRO PROVEEDOR"), ("22222222", "JUAN CARLOS RAMIREZ")], "22222222")
    assert picked == "NIT 22222222 - JUAN CARLOS RAMIREZ"


def test_missing_provider_raises(browser):
    with pytest.raises(ProviderNotFound, match="no existe"):
        pick(browser, [("222222221", "OTRO PROVEEDOR")], "22222222")


def test_list_that_never_shows_counts_as_missing(browser):
    with pytest.raises(ProviderNotFound, match="no existe"):
        pick(browser, [("22222222", "JUAN CARLOS RAMIREZ")], "22222222", shows_on_keyup=False)


def test_duplicate_exact_ids_stop(browser):
    with pytest.raises(ValueError, match="Varias"):
        pick(browser, [("22222222", "A"), ("22222222", "B")], "22222222")


def test_same_id_twice_picks_the_silogtran_type(browser):
    page = browser.new_page()
    items = '<li><a href="#">NIT <strong>22222222</strong> - JUAN</a></li><li><a href="#">CC <strong>22222222</strong> - JUAN</a></li>'
    page.set_content(PAGE % (items, "true"))
    try:
        pick_provider_suggestion(page, page.locator("#identificacionAdquiriente"), "22222222", prefer="NIT")
        assert page.inner_text("#picked") == "NIT 22222222 - JUAN"
    finally:
        page.close()
