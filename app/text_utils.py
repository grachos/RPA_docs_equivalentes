"""Pure text helpers: totals, Spanish dates, name normalization."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

SPANISH_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def parse_total(value: object) -> int:
    """Convert an Excel total (number or text like '3.250.000' / '$ 3,250,000.00') to integer pesos."""
    if value is None or isinstance(value, bool):
        raise ValueError("total vacío")
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
    else:
        text = re.sub(r"[\s$]|COP", "", str(value), flags=re.IGNORECASE)
        if not text:
            raise ValueError("total vacío")
        if not re.fullmatch(r"-?[\d.,]+", text):
            raise ValueError(f"total no numérico: {value!r}")
        amount = Decimal(_normalize_number_text(text))
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _normalize_number_text(text: str) -> str:
    """Decide which of '.' / ',' is the decimal separator and return a plain decimal string."""
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        return text.replace(thousands_sep, "").replace(decimal_sep, ".")
    sep = "." if has_dot else "," if has_comma else None
    if sep is None:
        return text
    # A single separator followed by groups of exactly three digits is a thousands separator.
    if re.fullmatch(rf"-?\d{{1,3}}(\{sep}\d{{3}})+", text):
        return text.replace(sep, "")
    if text.count(sep) == 1:
        return text.replace(sep, ".")
    raise ValueError(f"formato de total ambiguo: {text!r}")


def parse_user_date(text: str) -> date:
    """Accept 'DD/MM/AAAA', 'DD-MM-AAAA' or ISO 'AAAA-MM-DD'."""
    text = text.strip()
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    try:
        if match:
            day, month, year = (int(g) for g in match.groups())
            return date(year, month, day)
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Fecha inválida: {text!r} (use DD/MM/AAAA)") from exc


def spanish_month(d: date) -> str:
    return SPANISH_MONTHS[d.month - 1]


def format_observation(d: date, template: str, consolidado: str = "") -> str:
    """Render e.g. '{consolidado} {dd} {month} {yyyy}' -> 'CORTE 103 22 octubre 2026'."""
    text = template.format(dd=f"{d.day:02d}", month=spanish_month(d), yyyy=f"{d.year:04d}",
                           consolidado=" ".join(consolidado.split()))
    return " ".join(text.split())


def normalize_text(text: str | None) -> str:
    """Uppercase, strip accents and collapse whitespace, for comparisons."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(no_accents.upper().split())


def names_match(a: str | None, b: str | None) -> bool:
    """Same words in any order: the Excel often lists surnames first."""
    return sorted(normalize_text(a).split()) == sorted(normalize_text(b).split())


def fix_first_names(nombres: str, apellidos: str) -> str:
    """Remove the trailing surname(s) MyE-invoice wrongly appends to 'Nombres'.

    'ANA MARIA PEREZ LOPEZ' + 'PEREZ LOPEZ' -> 'ANA MARIA'
    'ANA ROSA SUAREZ VEGA SUAREZ VEGA' + 'SUAREZ VEGA' -> 'ANA ROSA'
    'LUIS TORRES' + 'TORRES MORA' -> 'LUIS'  (first surname leaked into Nombres)
    Returns the original tokens (accents preserved) of the given names; never empty.
    """
    tokens = nombres.split()
    surname = normalize_text(apellidos).split()
    if not surname:
        return " ".join(tokens)
    n = len(surname)
    while len(tokens) > n and [normalize_text(t) for t in tokens[-n:]] == surname:
        tokens = tokens[:-n]
    # Partial leak: Nombres ends with the first surname(s), e.g. "LUIS TORRES" / "TORRES MORA".
    for k in range(min(n, len(tokens) - 1), 0, -1):
        if [normalize_text(t) for t in tokens[-k:]] == surname[:k]:
            tokens = tokens[:-k]
            break
    return " ".join(tokens)


def de_number_from_filename(name: str) -> str | None:
    """'DE25_2264.zip' -> 'DE252264'. Only for cross-checks: the platform number wins."""
    match = re.search(r"(DE\d{2})_(\d+)", name, flags=re.IGNORECASE)
    return f"{match.group(1).upper()}{match.group(2)}" if match else None


def mask_id(value: str, visible: int = 3) -> str:
    """Partially mask an ID for logs: '11111111' -> '*****111'."""
    if len(value) <= visible:
        return value
    return "*" * (len(value) - visible) + value[-visible:]
