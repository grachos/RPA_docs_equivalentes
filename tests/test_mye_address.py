"""Applying Silogtran's provider data on a page that mimics MyE's acquirer section."""
import pytest
from playwright.sync_api import sync_playwright

from app.mye_acquirer import missing_address
from app.mye_tercero import FIXED_DELIVERY_EMAIL, apply_tercero, clean_address, mismatches, nit_dv
from app.terceros_api import Tercero

PLACE = """
<select id="paisAdquiriente{s}"><option value="-1">Seleccione</option><option value="CO">Colombia</option></select>
<select id="departamentosAdquiriente{s}"><option value="0">Seleccione</option></select>
<select id="ciudadesAdquiriente{s}"><option value="-1">Seleccione</option></select>
<input id="direccionAdquiriente{s}"><input id="codPostalAdquiriente{s}">
"""
PAGE = """
<select id="tiposDocumentosAdquiriente"><option value="13" selected>CC</option><option value="31">NIT</option></select>
<select id="tiposPersonaAdquiriente"><option value="1" selected>Persona jurídica</option><option value="2">Persona natural</option></select>
<input id="digitoVerificacionAdquiriente">
<input id="nombresAdquiriente" value="JUAN CARLOS RAMIREZ"><input id="apellidosAdquiriente">
<input id="razonSocialAdquiriente" value="JUAN CARLOS RAMIREZ">
""" + PLACE.format(s="") + """
<input id="emailContactoAdquiriente"><input id="telefonoAdquiriente" value="3009999999">
<input id="emailEntregaAdquiriente" value="propio@x.com"><input id="emailEntregaAdquirienteConfirm" value="propio@x.com">
<input type="checkbox" id="guardarEmpresaSede">
""" + PLACE.format(s="Fiscal") + """
<input id="nombreContactoAdq" value="ANGIE"><input id="correoContactoAdq"><input id="telefonoContactoAdq"><input id="notasContactoAdq">
<table><tr id="obligacionadq_O-13"><td><input type="checkbox" checked></td></tr>
<tr id="obligacionadq_R-99-PN"><td><input type="checkbox"></td></tr></table>
<script>
// Minimal jQuery-like shim: val() + trigger('change') + hide(), enough for the app code.
window.$ = s => { const els = [...document.querySelectorAll(s)];
  const api = { first: () => api, hide: () => api,
    val: v => { els.forEach(e => e.value = v); return api; },
    trigger: () => { els.forEach(e => e.dispatchEvent(new Event('change'))); return api; } };
  return api; };
const opts = (sel, list) => { document.querySelector(sel).innerHTML = list.map(([v, t]) =>
  `<option value="${v}">${t}</option>`).join(''); };
// Like MyE: catalogs load asynchronously after each change, values are DANE codes.
for (const s of ['', 'Fiscal']) {
  document.getElementById('paisAdquiriente' + s).addEventListener('change', () => setTimeout(() =>
    opts('#departamentosAdquiriente' + s, [['0', 'Seleccione'], ['05', 'Antioquia'], ['11', 'Bogotá']]), 200));
  document.getElementById('departamentosAdquiriente' + s).addEventListener('change', e => setTimeout(() =>
    opts('#ciudadesAdquiriente' + s, e.target.value === '11'
      ? [['-1', 'Seleccione'], ['001', 'Bog'], ['000000011001', 'Bog'], ['11001', 'Bogotá, D.c.']]
      : [['-1', 'Seleccione'], ['05360', 'Itagüí']]), 200));
}
// Like MyE: changing the type clears names and DV; CC also forces Persona natural (disabled).
document.getElementById('tiposDocumentosAdquiriente').addEventListener('change', e => {
  const p = document.getElementById('tiposPersonaAdquiriente');
  if (e.target.value === '13') { p.value = '2'; p.disabled = true; } else { p.disabled = false; }
  for (const id of ['nombresAdquiriente', 'apellidosAdquiriente', 'digitoVerificacionAdquiriente'])
    document.getElementById(id).value = '';
});
</script>
"""
ADDRESS = {"País": "#paisAdquiriente", "Departamento": "#departamentosAdquiriente",
           "Ciudad": "#ciudadesAdquiriente", "Dirección": "#direccionAdquiriente",
           "Código postal": "#codPostalAdquiriente"}


def tercero(dane="11001000", **kw):
    data = dict(documento="22222222", tipo_documento="CEDULA", tipo_persona="NATURAL", nombre="JUAN CARLOS RAMIREZ",
                pais="Colombia", departamento="Bogotá D.C.", ciudad="BOGOTA", direccion="CALLE 29A N 9 75",
                codigo_postal=dane[:5], email="jhon@x.com", telefono="3001234567",
                email_entrega="informacionalproveedor@elogia.com.co", ciudad_dane=dane,
                nombre1="JUAN", nombre2="CARLOS", apellido1="RAMIREZ", apellido2="")
    data.update(kw)
    return Tercero(**data)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p = browser.new_page()
    p.set_content(PAGE)
    yield p
    p.close()


def values(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('select, input')]
        .filter(e => e.id).map(e => [e.id, e.type === 'checkbox' ? e.checked : e.value]))""")


def test_dv_matches_known_values():
    assert (nit_dv("22222222"), nit_dv("44444444"), nit_dv("819004116"), nit_dv("800150249")) == ("1", "2", "5", "1")


def test_always_nit_with_dv_and_silogtran_data(page):
    assert missing_address(page, ADDRESS, wait_rounds=1) == list(ADDRESS)
    changes = apply_tercero(page, tercero())
    v = values(page)
    assert (v["tiposDocumentosAdquiriente"], v["tiposPersonaAdquiriente"], v["digitoVerificacionAdquiriente"])         == ("31", "2", "1")
    assert (v["nombresAdquiriente"], v["apellidosAdquiriente"]) == ("JUAN CARLOS", "RAMIREZ")
    for s in ("", "Fiscal"):
        assert (v[f"paisAdquiriente{s}"], v[f"departamentosAdquiriente{s}"], v[f"ciudadesAdquiriente{s}"]) \
            == ("CO", "11", "11001")
        assert v[f"direccionAdquiriente{s}"] == "CALLE 29A N 9 75" and v[f"codPostalAdquiriente{s}"] == "11001"
    assert v["telefonoAdquiriente"] == "3001234567" and v["emailContactoAdquiriente"] == "jhon@x.com"
    assert v["emailEntregaAdquiriente"] == v["emailEntregaAdquirienteConfirm"] == FIXED_DELIVERY_EMAIL
    assert v["nombreContactoAdq"] == ""
    obligations = page.evaluate("[...document.querySelectorAll('tr input')].map(c => c.checked)")
    assert obligations == [False, True]
    assert "Tipo documento" in changes and "Obligaciones (R-99-PN)" in changes
    assert mismatches(page, tercero()) == []
    assert apply_tercero(page, tercero()) == []  # already matching: nothing to change


def test_other_department_uses_its_dane_code(page):
    apply_tercero(page, tercero("05360000"))
    v = values(page)
    assert (v["departamentosAdquiriente"], v["ciudadesAdquiriente"], v["codPostalAdquiriente"]) == ("05", "05360", "05360")
    assert v["ciudadesAdquirienteFiscal"] == "05360"


def test_juridica_uses_business_name(page):
    apply_tercero(page, tercero(tipo_documento="NIT", tipo_persona="JURIDICA", nombre="TRANSPORTES SAS"))
    v = values(page)
    assert (v["tiposDocumentosAdquiriente"], v["tiposPersonaAdquiriente"]) == ("31", "1")
    assert v["digitoVerificacionAdquiriente"] == "1"
    assert v["razonSocialAdquiriente"] == "TRANSPORTES SAS"


def test_mismatch_is_reported(page):
    apply_tercero(page, tercero())
    page.evaluate("document.getElementById('emailEntregaAdquiriente').value = 'otro@x.com'")
    assert mismatches(page, tercero()) == ["Email entrega"]


def test_city_not_in_mye_raises(page):
    with pytest.raises(ValueError, match="11999"):
        apply_tercero(page, tercero("11999000"))  # department exists, city does not


def test_missing_data_raises(page):
    with pytest.raises(ValueError, match="DANE"):
        apply_tercero(page, tercero(dane=""))
    with pytest.raises(ValueError, match="Tipo de persona"):
        apply_tercero(page, tercero(tipo_documento="XYZ", tipo_persona=""))
    with pytest.raises(ValueError, match="nombres y apellidos"):
        apply_tercero(page, tercero(nombre1="", nombre2=""))


def test_address_punctuation_invalid_for_mye_is_cleaned(page):
    assert clean_address("CASA 4 MANZANA F BARRIO: VILLA ESPERANZA") == "CASA 4 MANZANA F BARRIO VILLA ESPERANZA"
    assert clean_address("CALLE 29A # 9-75, TORRE 2") == "CALLE 29A # 9-75, TORRE 2"
    t = tercero(direccion="CASA 4 BARRIO: VILLA", fiscal_direccion="CASA 4 BARRIO: VILLA")
    apply_tercero(page, t)
    v = values(page)
    assert v["direccionAdquiriente"] == v["direccionAdquirienteFiscal"] == "CASA 4 BARRIO VILLA"
    assert mismatches(page, t) == []


def test_inconsistent_data_dialog_fails_fast_with_mye_message(page):
    from app.myeinvoice import MyeError, MyeInvoice

    page.set_content("""<div class="modal show"><button class="close" data-dismiss="modal">x</button>
        Datos inconsistentes - La dirección del adquiriente tiene caracteres inválidos</div>
        <script>window.$ = () => ({modal: () => {}});</script>""")
    mye = MyeInvoice.__new__(MyeInvoice)
    mye.page = page
    with pytest.raises(MyeError, match="caracteres inválidos"):
        mye._raise_if_inconsistent(wait_ms=2000)


def test_bad_contact_email_fails_before_filling(page):
    for bad in ("persona@ejemplo.con", "sin-arroba.com", "a b@x.com"):
        with pytest.raises(ValueError, match="Correo de contacto inválido"):
            apply_tercero(page, tercero(email=bad))
    apply_tercero(page, tercero(email="ok@elogia.com.co"))
