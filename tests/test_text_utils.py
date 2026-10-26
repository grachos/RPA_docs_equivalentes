from datetime import date

import pytest

from app.text_utils import (
    de_number_from_filename,
    fix_first_names,
    format_observation,
    mask_id,
    names_match,
    normalize_text,
    parse_total,
    parse_user_date,
)


@pytest.mark.parametrize("value, expected", [
    (3250000, 3250000),
    (3250000.0, 3250000),
    ("3.250.000", 3250000),
    ("3,250,000", 3250000),
    ("$ 3.250.000", 3250000),
    ("3.250.000,00", 3250000),
    ("3,250,000.00", 3250000),
    ("3250000,5", 3250001),
    ("1.5", 2),
    (" 850000 ", 850000),
])
def test_parse_total(value, expected):
    assert parse_total(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", "1.2.3", True])
def test_parse_total_invalid(value):
    with pytest.raises(ValueError):
        parse_total(value)


def test_format_observation_templates():
    d = date(2026, 10, 9)
    assert format_observation(d, "corte {dd} {month} {yyyy}") == "corte 09 octubre 2026"
    assert format_observation(d, "corte {dd} de {month} {yyyy}") == "corte 09 de octubre 2026"
    assert format_observation(date(2026, 1, 31), "{month}") == "enero"
    assert format_observation(date(2026, 12, 1), "{month}") == "diciembre"
    assert format_observation(date(2026, 10, 22), "{consolidado} {dd} {month} {yyyy}",
                              " CORTE  103 ") == "CORTE 103 22 octubre 2026"


def test_normalize_text():
    assert normalize_text("  Ana  Rosa suarez ") == "ANA ROSA SUAREZ"
    assert normalize_text(None) == ""
    assert names_match("José Pérez", "JOSE PEREZ")
    assert names_match("PEDRO LOPEZ DIAZ", "LOPEZ DIAZ PEDRO")
    assert not names_match("JUAN CARLOS", "JUAN CARLOS RAMIREZ")


@pytest.mark.parametrize("nombres, apellidos, expected", [
    ("ANA MARIA PEREZ LOPEZ", "PEREZ LOPEZ", "ANA MARIA"),
    ("ANA ROSA SUAREZ VEGA SUAREZ VEGA", "SUAREZ VEGA", "ANA ROSA"),
    ("ANA ROSA SUAREZ VEGA", "Suárez Vega", "ANA ROSA"),
    ("ANA MARIA", "PEREZ LOPEZ", "ANA MARIA"),
    ("PEREZ LOPEZ", "PEREZ LOPEZ", "PEREZ LOPEZ"),  # never empties the field
    ("PEDRO", "", "PEDRO"),
    ("LUIS TORRES", "TORRES MORA", "LUIS"),  # first surname leaked
    ("LUIS TORRES TORRES MORA", "TORRES MORA", "LUIS"),
    ("TORRES", "TORRES MORA", "TORRES"),  # never empties the field
])
def test_fix_first_names(nombres, apellidos, expected):
    assert fix_first_names(nombres, apellidos) == expected


def test_de_number_from_filename():
    assert de_number_from_filename("DE25_2264.zip") == "DE252264"
    assert de_number_from_filename("ad0819004116.pdf") is None


def test_mask_id():
    assert mask_id("11111111") == "*****111"
    assert mask_id("12") == "12"


@pytest.mark.parametrize("text, expected", [
    ("09/10/2026", date(2026, 10, 9)),
    ("9-10-2026", date(2026, 10, 9)),
    ("2026-10-09", date(2026, 10, 9)),
])
def test_parse_user_date(text, expected):
    assert parse_user_date(text) == expected


@pytest.mark.parametrize("text", ["31/02/2026", "10/2026", ""])
def test_parse_user_date_invalid(text):
    with pytest.raises(ValueError):
        parse_user_date(text)
