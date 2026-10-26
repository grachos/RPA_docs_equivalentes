"""Configuration: paths, business constants and secure credential loading.

Credentials are never hard-coded. They are read from `.env` (git-ignored) or,
if missing there, from Windows Credential Manager via `keyring`.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value and value.strip().isdigit() else default


# --- Paths -----------------------------------------------------------------
STATE_DB = BASE_DIR / os.getenv("STATE_DB", "state.db")
DOWNLOADS_DIR = BASE_DIR / "downloads"
DEBUG_DIR = BASE_DIR / "debug"
LOGS_DIR = BASE_DIR / "logs"
REPORTS_DIR = BASE_DIR / "reports"
BROWSER_PROFILE_DIR = BASE_DIR / ".browser_profile"

# --- Sites -----------------------------------------------------------------
MYE_LOGIN_URL = "https://portal.myeinvoice.co/CompunetWebService/interfaces/inicio.jsp"
MYE_BASE_URL = MYE_LOGIN_URL.rsplit("/", 1)[0] + "/"
MYE_INVOICE_PAGE = "entrada.jsp?page=facturarManualFaseII.html"
MYE_DOCS_PAGE = "entrada.jsp?page=administrarDocElectronicos.html"
CADI_LOGIN_URL = "https://cadi.com.co/cadi/lgc/users/login_public"
CADI_HOME_URL = "https://cadi.com.co/cadi/"
CADI_CREATE_URL = "https://cadi.com.co/cadi/accounting/invoices/create"

# --- MyE-invoice constants ---------------------------------------------------
MYE_COMPANY_NIT = "819004116-5"
# Confirmed live on 2026-10-22. Beware: "Factura - DE25 - 8951" also exists.
MYE_DOCUMENT_TYPE = os.getenv("MYE_DOCUMENT_TYPE", "Documento Soporte - DE25 - 8759")
MYE_CONCEPT_CODE = "4220"
MYE_CONCEPT_DESCRIPTION = "SERVICIO CARGA TERCERIZADA"
MYE_ATTACHMENT_TYPE = "Orden de compra"
# Exact option text; a similar "DOCUMENTO SOPORTE - ..._fe1.jrxml" also exists.
MYE_GRAPHIC_REPRESENTATION = "DOCUMENTO SOPORTE DS - factura_myenvoice_DocumentoSoporte.jrxml"
MYE_PAYMENT_METHOD = "Crédito"
MYE_PAYMENT_MEANS = "(45) Transferencia Crédito Bancario"
MYE_DIAN_OK_STATUS = "Procesado Correctamente"
MYE_DIAN_PENDING_STATUS = "Listo Para Enviar"
# The DIAN send is done by MyE's own monitor (user decision: the app never presses
# "Enviar a la DIAN"); the app polls until "Procesado Correctamente" or the time limit.
DIAN_MAX_WAIT_SECONDS = _env_int("DIAN_MAX_WAIT_SECONDS", 600)
DIAN_POLL_SECONDS = _env_int("DIAN_POLL_SECONDS", 15)
# Days before the corte date searched for an existing document of the same provider/amount.
DUPLICATE_LOOKBACK_DAYS = _env_int("DUPLICATE_LOOKBACK_DAYS", 60)

# --- CADI constants ----------------------------------------------------------
CADI_LIST_URL = "https://cadi.com.co/cadi/accounting/invoices"
CADI_INVOICE_KIND = "Factura"
CADI_COMPANY_SEARCH = "da25"
CADI_COMPANY = "DA25 - Elogia Solucione S.A.S."
CADI_INVOICE_TYPE = "FAC ELEC - TERCERIZACION"
CADI_CENTER = "OPERATIVO"
CADI_REVIEWER = os.getenv("CADI_REVIEWER", "")  # "<cédula> - <NOMBRE>", required: set it in .env
CADI_INVOICE_NUMBER_MAX_LEN = 16

# --- Observation templates ---------------------------------------------------
# Placeholders: {consolidado} name typed in the app, {dd} two-digit day,
# {month} Spanish month (lowercase), {yyyy} year.
MYE_OBSERVATION_TEMPLATE = os.getenv("MYE_OBSERVATION_TEMPLATE", "{consolidado} {dd} {month} {yyyy}")
CADI_OBSERVATION_TEMPLATE = os.getenv("CADI_OBSERVATION_TEMPLATE", "{consolidado} {dd} {month} {yyyy}")

# --- Timeouts ----------------------------------------------------------------
TIMEOUT_MS = _env_int("TIMEOUT_MS", 30_000)
NETWORK_RETRIES = _env_int("NETWORK_RETRIES", 3)

# --- Excel -------------------------------------------------------------------
OC_LENGTH = 10

# Documents created during manual testing. Never modify them; idempotency
# checks must detect them as already existing.
PROTECTED_DE_NUMBERS = frozenset({"DE252263", "DE252264"})
PROTECTED_CADI_IDS = frozenset({"233998", "234004"})

# --- Credentials -------------------------------------------------------------
KEYRING_SERVICE = "doc-soporte-auto"
CREDENTIAL_NAMES = ("MYE_USER", "MYE_PASSWORD", "CADI_DOCUMENT", "CADI_PASSWORD", "TERCEROS_API_KEY")

# Silogtran "terceros" API: address/identification of providers missing data in MyE.
TERCEROS_API_URL = os.getenv("TERCEROS_API_URL", "https://apis-legalizacion-silogtran.onrender.com")


def save_credential(name: str, value: str, env_path: Path | None = None) -> Path:
    """Store a credential in the project's .env (git-ignored) and apply it to this process."""
    if name not in CREDENTIAL_NAMES:
        raise ValueError(f"Unknown credential: {name}")
    if not value or "\n" in value:
        raise ValueError("Valor vacío o con saltos de línea.")
    from dotenv import set_key

    path = env_path or ENV_PATH
    path.touch(exist_ok=True)
    set_key(path, name, value, quote_mode="always")
    os.environ[name] = value
    return path


def get_credential(name: str) -> str:
    """Return a credential from .env or Windows Credential Manager.

    Raises RuntimeError if it is not configured. The value is never logged.
    """
    if name not in CREDENTIAL_NAMES:
        raise ValueError(f"Unknown credential: {name}")
    value = os.getenv(name)
    if not value:
        try:
            import keyring

            value = keyring.get_password(KEYRING_SERVICE, name)
        except Exception:  # keyring backend missing or unavailable
            value = None
    if not value:
        raise RuntimeError(
            f"Credencial {name} no configurada. Guárdela en la pestaña Configuración "
            f"o con `python -m app.main set-credential {name}`."
        )
    return value
