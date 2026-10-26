from app.models import Row
from app.pdf_check import check_pdf_text

ROW = Row(excel_row=2, provider_id="12345678", provider_name="MARIA JOSÉ PÉREZ GÓMEZ",
          oc="4600000001", total=1770000)
OBS = "corte 09 octubre 2026"

# Same layout as the MyE preview PDF (line breaks inside names, glued labels).
GOOD = """ELOGIA SOLUCIONES LOGÍSTICAS
CUDS:
MARIA JOSÉ PÉREZ
GÓMEZVendedor de Bien o
NIT 12345678-1
Documento Soporte DE250
4600000001
SERVICIO CARGA TERCERIZADA 1 $1.770.000,00 $1.770.000,004220
Total $1.770.000,00
Observaciones:corte 09 octubre 2026
"""


def test_good_preview_passes():
    assert check_pdf_text(GOOD, ROW, OBS) == []


def test_other_provider_is_detected():
    bad = GOOD.replace("MARIA JOSÉ PÉREZ\nGÓMEZ", "ANA ROSA SUAREZ VEGA").replace("12345678", "55555555")
    assert any("ID" in p for p in check_pdf_text(bad, ROW, OBS))  # the ID always decides
    problems = check_pdf_text(bad, ROW, OBS, acquirer_name="MARIA JOSÉ PÉREZ GÓMEZ")
    assert any("nombre" in p for p in problems)


def test_incomplete_excel_name_with_same_id_passes():
    # User rule: the Excel name may be incomplete or differ; MyE's provider for the ID wins.
    row = ROW.model_copy(update={"provider_name": "MARIA PEREZ G"})
    assert check_pdf_text(GOOD, row, OBS, acquirer_name="MARIA JOSÉ PÉREZ GÓMEZ",
                          acquirer_surnames="PÉREZ GÓMEZ") == []


def test_duplicated_surname_is_detected():
    bad = GOOD.replace("GÓMEZVendedor", "GÓMEZ PÉREZ GÓMEZVendedor")
    assert any("duplicado" in p for p in check_pdf_text(bad, ROW, OBS))
    assert any("duplicado" in p for p in check_pdf_text(bad, ROW, OBS, acquirer_surnames="PÉREZ GÓMEZ"))


def test_wrong_total_observation_oc():
    bad = GOOD.replace("1.770.000", "1.700.000").replace("09 octubre", "10 octubre").replace("4600000001", "4600000002")
    problems = " ".join(check_pdf_text(bad, ROW, OBS))
    assert "total" in problems and "observación" in problems and "OC" in problems


def test_de_number_with_underscore():
    text = GOOD + "\nDE25_2264"
    assert check_pdf_text(text, ROW, OBS, de_number="DE252264") == []
    assert check_pdf_text(GOOD, ROW, OBS, de_number="DE252265")
