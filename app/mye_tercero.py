"""Make the MyE-invoice acquirer match Silogtran, the master data of providers.

Rules (API docs, github.com/grachos/apis_legalizacion_silogtran, "Terceros"):
- tipo de documento is ALWAYS NIT (31) with its DV: the DIAN rejects a documento soporte whose
  provider is not NIT ("DSAJ25a: El contenido de este atributo no corresponde a '31'", seen on
  DE252291–DE252300 sent as CC on 2026-10-26), even for a natural person with a cédula;
- tipo de persona / nombres come from Silogtran (MyE may hold a natural person as Persona
  jurídica: it is corrected);
- residence address by DANE code; fiscal address = residence;
- email de entrega + confirmación FIXED (informacionalproveedor@elogia.com.co);
- contact data (nombre/correo/teléfono/notas contacto) empty;
- obligations: only R-99-PN.
Recon 2026-10-26 (scripts/probe_mye8.py): changing the document type CLEARS Nombres/Apellidos/DV,
so names and DV are written after the type. MyE saves the acquirer per (type, ID) at preview time.
"""
from __future__ import annotations

import logging
import re

from playwright.sync_api import Page

from app.mye_acquirer import _set, _value, _wait_option
from app.terceros_api import Tercero
from app.text_utils import normalize_text

log = logging.getLogger("doc_soporte")

# MyE rejects the preview ("La dirección del adquiriente tiene caracteres inválidos") when the address
# has punctuation such as ":" (seen 2026-10-26: "CASA 4 MANZANA F BARRIO: VILLA ESPERANZA").
_INVALID_ADDRESS_CHARS = re.compile(r"[^\w #\-.,/°]", re.UNICODE)


_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_TYPO_TLDS = {"con", "comm", "coom", "ocm", "vom", "xom"}


def check_email(email: str | None, who: str) -> None:
    """MyE/DIAN reject a malformed contact email only at preview (~1 min later): fail early instead."""
    if not email:
        return
    if not _EMAIL.fullmatch(email.strip()) or email.strip().rsplit(".", 1)[-1].lower() in _TYPO_TLDS:
        raise ValueError(f"Correo de contacto inválido en Silogtran para {who}: {email!r}.")


def clean_address(address: str) -> str:
    return " ".join(_INVALID_ADDRESS_CHARS.sub(" ", address or "").replace("_", " ").split())


FIXED_DELIVERY_EMAIL = "informacionalproveedor@elogia.com.co"
OBLIGATION = "R-99-PN"
SEL_DOC_TYPE = "#tiposDocumentosAdquiriente"
SEL_PERSON_TYPE = "#tiposPersonaAdquiriente"
CONTACT_FIELDS = ("#nombreContactoAdq", "#correoContactoAdq", "#telefonoContactoAdq", "#notasContactoAdq")
DOC_TYPE_NIT = "31"  # the only type the DIAN accepts for the provider of a documento soporte
DV_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)
PERSON_TYPES = {"NATURAL": "2", "PERSONA NATURAL": "2", "JURIDICA": "1", "PERSONA JURIDICA": "1"}


def nit_dv(number: str) -> str:
    """DIAN check digit of a NIT/cédula (weights 3, 7, 13… from the rightmost digit)."""
    digits = re.sub(r"\D", "", number)
    if not digits or len(digits) > len(DV_WEIGHTS):
        raise ValueError(f"Identificación inválida para calcular el DV: {number!r}")
    r = sum(int(d) * w for d, w in zip(reversed(digits), DV_WEIGHTS)) % 11
    return str(11 - r if r > 1 else r)


def _join(*parts: str) -> str:
    return " ".join(" ".join(p.split()) for p in parts if p and p.strip())


def expected_values(t: Tercero) -> dict[str, tuple[str, str]]:
    """{label: (selector, value)} the form must hold. Raises ValueError if Silogtran lacks data."""
    person = PERSON_TYPES.get(normalize_text(t.tipo_persona))         or ("2" if normalize_text(t.tipo_documento).startswith("CEDULA") else "")
    if not person:
        raise ValueError(f"Tipo de persona de Silogtran {t.tipo_persona!r} sin equivalente en MyE.")
    dane = (t.ciudad_dane or "").strip()
    if not re.fullmatch(r"\d{5,8}", dane):
        raise ValueError(f"Silogtran no tiene código DANE de la ciudad de {t.documento}.")
    if not t.direccion:
        raise ValueError(f"Silogtran no tiene dirección para {t.documento}.")
    values = {"Tipo documento": (SEL_DOC_TYPE, DOC_TYPE_NIT), "Tipo persona": (SEL_PERSON_TYPE, person),
              "DV": ("#digitoVerificacionAdquiriente", nit_dv(t.documento))}
    if person == "2":
        names, surnames = _join(t.nombre1, t.nombre2), _join(t.apellido1, t.apellido2)
        if not names or not surnames:
            raise ValueError(f"Silogtran no trae nombres y apellidos separados de {t.documento}.")
        values["Nombres"] = ("#nombresAdquiriente", names)
        values["Apellidos"] = ("#apellidosAdquiriente", surnames)
    else:
        values["Razón social"] = ("#razonSocialAdquiriente", t.nombre)
    email_entrega = t.email_entrega or FIXED_DELIVERY_EMAIL
    for suffix, label, address, postal in (("", "", t.direccion, t.codigo_postal or dane[:5]),
                                          ("Fiscal", " fiscal", t.fiscal_direccion or t.direccion,
                                           t.fiscal_codigo_postal or t.codigo_postal or dane[:5])):
        values[f"País{label}"] = (f"#paisAdquiriente{suffix}", "CO")
        values[f"Departamento{label}"] = (f"#departamentosAdquiriente{suffix}", dane[:2])
        values[f"Ciudad{label}"] = (f"#ciudadesAdquiriente{suffix}", dane[:5])
        values[f"Dirección{label}"] = (f"#direccionAdquiriente{suffix}", clean_address(address))
        values[f"Código postal{label}"] = (f"#codPostalAdquiriente{suffix}", postal)
    check_email(t.email, t.documento)
    values["Email contacto"] = ("#emailContactoAdquiriente", t.email)
    values["Teléfono"] = ("#telefonoAdquiriente", t.telefono)
    values["Email entrega"] = ("#emailEntregaAdquiriente", email_entrega)
    values["Confirmar email entrega"] = ("#emailEntregaAdquirienteConfirm", email_entrega)
    for i, sel in enumerate(CONTACT_FIELDS):
        values[f"Contacto {i + 1}"] = (sel, "")
    return values


def suggestion_prefix(t: Tercero) -> str:
    """Typeahead entry to use when MyE lists the ID twice ("NIT 9448…" and "CC 9448…")."""
    return "NIT"


def _set_cascade(page: Page, values: dict, label: str, changes: list[str]) -> None:
    """País -> Departamento -> Ciudad: each change reloads the next select's options."""
    for part in ("País", "Departamento", "Ciudad"):
        sel, value = values[f"{part}{label}"]
        if _value(page, sel) == value:
            continue
        if not _wait_option(page, sel, value):
            raise ValueError(f"MyE no tiene {part.lower()}{label} con código {value}.")
        _set(page, sel, value)
        changes.append(f"{part}{label}")


def _obligations(page: Page) -> bool:
    """Leave only R-99-PN checked (clicks run MyE's own onclick handler). True if changed."""
    return page.evaluate("""keep => {
      let changed = false;
      document.querySelectorAll('tr[id^="obligacionadq_"] input[type=checkbox]').forEach(cb => {
        const want = cb.closest('tr').id === 'obligacionadq_' + keep;
        if (cb.checked !== want) { cb.click(); changed = true; }
      });
      const sede = document.querySelector('#guardarEmpresaSede');
      if (sede && sede.checked) { sede.click(); changed = true; }
      return changed;
    }""", OBLIGATION)


def apply_tercero(page: Page, t: Tercero) -> list[str]:
    """Write Silogtran's data over the acquirer section. Returns the labels that changed."""
    values = expected_values(t)
    changes: list[str] = []
    sel, doc = values["Tipo documento"]
    if _value(page, sel) != doc:
        _set(page, sel, doc)  # clears Nombres/Apellidos/DV in MyE
        page.wait_for_timeout(1500)
        changes.append("Tipo documento")
    sel, person = values["Tipo persona"]
    if _value(page, sel) != person:
        page.evaluate("s => { const e = document.querySelector(s); e.disabled = false; }", sel)
        _set(page, sel, person)
        page.wait_for_timeout(1000)
        changes.append("Tipo persona")
    _set_cascade(page, values, "", changes)
    _set_cascade(page, values, " fiscal", changes)
    skip = {f"{p}{s}" for p in ("País", "Departamento", "Ciudad") for s in ("", " fiscal")}
    skip |= {"Tipo documento", "Tipo persona"}
    for label, (sel, value) in values.items():
        if label not in skip and _value(page, sel) != value:
            _set(page, sel, value)
            changes.append(label)
    if _obligations(page):
        changes.append(f"Obligaciones ({OBLIGATION})")
    page.wait_for_timeout(500)
    log.info("Proveedor %s: datos de Silogtran aplicados: %s", t.documento, ", ".join(changes) or "sin cambios")
    return changes


def mismatches(page: Page, t: Tercero) -> list[str]:
    """Fields that do not hold Silogtran's value (read back before previewing)."""
    wrong = [label for label, (sel, value) in expected_values(t).items() if _value(page, sel) != value]
    ok = page.evaluate("""keep => [...document.querySelectorAll('tr[id^="obligacionadq_"] input')]
        .every(cb => cb.checked === (cb.closest('tr').id === 'obligacionadq_' + keep))""", OBLIGATION)
    return wrong + ([] if ok else ["Obligaciones"])


def apply_until_stable(page: Page, t: Tercero, rounds: int = 3) -> list[str]:
    """Apply Silogtran's data until it holds: a late provider load in MyE (e.g. after the
    suggestion list was reopened) can overwrite it once more (seen live 2026-10-26)."""
    changes: list[str] = []
    for _ in range(rounds):
        changes += [c for c in apply_tercero(page, t) if c not in changes]
        page.wait_for_timeout(2000)
        if not mismatches(page, t):
            break
    return changes
