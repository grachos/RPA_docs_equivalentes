"""Client of the "terceros" API (Silogtran, the master data of providers for MyE-invoice).

GET {TERCEROS_API_URL}/api/terceros/{documento}       -> "Información Económica"
GET {TERCEROS_API_URL}/api/terceros/{documento}/raw   -> names split (nombre1/2, apellido1/2)
Header x-api-key = TERCEROS_API_KEY (saved in .env from the Settings tab, never logged).
The service sleeps when idle (Render free plan): the first call can take ~1 minute.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from app import config

log = logging.getLogger("doc_soporte")

DOC_RE = re.compile(r"^[0-9A-Za-z-]{3,20}$")


class TerceroApiError(Exception):
    """The API could not answer (key, network, Silogtran). The row stops with this message."""


@dataclass
class Tercero:
    documento: str
    tipo_documento: str
    tipo_persona: str
    nombre: str
    pais: str
    departamento: str
    ciudad: str
    direccion: str
    codigo_postal: str
    email: str
    telefono: str
    email_entrega: str
    ciudad_dane: str | None = None
    obligaciones: list[str] = field(default_factory=list)
    nombre1: str = ""
    nombre2: str = ""
    apellido1: str = ""
    apellido2: str = ""
    fiscal_direccion: str = ""
    fiscal_codigo_postal: str = ""

    def missing_address(self) -> list[str]:
        """Address fields MyE requires that the API did not bring."""
        required = {"País": self.pais, "Departamento": self.departamento, "Ciudad": self.ciudad,
                    "Dirección": self.direccion, "Código postal": self.codigo_postal}
        return [k for k, v in required.items() if not (v or "").strip()]


def parse_tercero(info: dict, raw: dict | None = None) -> Tercero:
    """Build a Tercero from /api/terceros/{doc} (+ optional /raw for split names)."""
    ident = info.get("identificacion") or {}
    res = info.get("residenciaEntrega") or {}
    fiscal = info.get("direccionFiscal") or {}
    src = info.get("fuente") or {}
    t = (raw or {}).get("tercero") or {}
    return Tercero(
        documento=str(ident.get("documento") or "").strip(),
        tipo_documento=str(ident.get("tipoDocumento") or "").strip(),
        tipo_persona=str(ident.get("tipoPersona") or "").strip(),
        nombre=" ".join(str(ident.get("nombre") or "").split()),
        pais=str(res.get("pais") or "").strip(),
        departamento=str(res.get("departamento") or "").strip(),
        ciudad=str(res.get("ciudad") or "").strip(),
        direccion=" ".join(str(res.get("direccion") or "").split()),
        codigo_postal=str(res.get("codigoPostal") or "").strip(),
        email=str(res.get("email") or "").strip(),
        telefono=str(res.get("telefono") or "").strip(),
        email_entrega=str(res.get("emailEntrega") or "").strip(),
        ciudad_dane=src.get("ciudadDane"),
        obligaciones=[o.get("codigo", "") for o in info.get("obligacionesAplicables") or [] if o.get("codigo")],
        nombre1=str(t.get("nombre1") or "").strip(),
        nombre2=str(t.get("nombre2") or "").strip(),
        apellido1=str(t.get("apellido1") or "").strip(),
        apellido2=str(t.get("apellido2") or "").strip(),
        fiscal_direccion=" ".join(str(fiscal.get("direccion") or "").split()),
        fiscal_codigo_postal=str(fiscal.get("codigoPostal") or "").strip(),
    )


class TercerosApi:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: int = 120):
        self.base_url = (base_url or config.TERCEROS_API_URL).rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self._cache: dict[tuple[str, bool], Tercero | None] = {}
        self._lock = threading.Lock()

    def _key(self) -> str:
        if self._api_key is None:
            try:
                self._api_key = config.get_credential("TERCEROS_API_KEY")
            except RuntimeError as exc:
                raise TerceroApiError("Falta la API KEY de terceros: guárdela en la pestaña Configuración.") \
                    from exc
        return self._api_key

    def _get(self, path: str) -> dict | None:
        """GET JSON; None on 404 (the provider does not exist in Silogtran)."""
        request = urllib.request.Request(self.base_url + path,
                                         headers={"x-api-key": self._key(), "Accept": "application/json"})
        last: Exception | None = None
        for attempt in (1, 2):  # the service may be waking up (Render free plan)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = _error_text(exc)
                if exc.code == 404:
                    return None
                if exc.code == 401:
                    raise TerceroApiError("La API de terceros rechazó la API KEY (401).") from exc
                if exc.code == 409:
                    raise TerceroApiError(f"Varios terceros con ese documento en Silogtran: {body}") from exc
                if exc.code == 400:
                    raise TerceroApiError(f"Documento inválido para la API de terceros: {body}") from exc
                last = TerceroApiError(f"La API de terceros respondió {exc.code}: {body}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = TerceroApiError(f"No se pudo conectar con la API de terceros: {exc}")
            log.warning("Terceros API %s failed (attempt %s): %s", path.split("/")[-1], attempt, last)
        raise last  # type: ignore[misc]

    def prefetch(self, documentos: list[str]) -> None:
        """Warm the cache in the background (the service may be asleep; rows then don't wait)."""
        def one(doc: str) -> None:
            try:
                self.get(doc, with_names=True)
            except TerceroApiError as exc:  # the row will ask again and report it
                log.info("Terceros API prefetch %s: %s", doc, exc)
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="terceros")
        for doc in dict.fromkeys(documentos):
            pool.submit(one, doc)
        pool.shutdown(wait=False)

    def get(self, documento: str, with_names: bool = False) -> Tercero | None:
        """Provider data from Silogtran, or None if it does not exist there (cached per run)."""
        documento = documento.strip()
        for key in ((documento, True), (documento, with_names)):
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
        tercero = self._fetch(documento, with_names)
        with self._lock:
            self._cache[(documento, with_names)] = tercero
        return tercero

    def _fetch(self, documento: str, with_names: bool) -> Tercero | None:
        if not DOC_RE.match(documento):
            raise TerceroApiError(f"Documento inválido: {documento!r}")
        info = self._get(f"/api/terceros/{documento}")
        if info is None:
            return None
        raw = self._get(f"/api/terceros/{documento}/raw") if with_names else None
        tercero = parse_tercero(info, raw)
        if tercero.documento and tercero.documento != documento:
            raise TerceroApiError(f"La API devolvió el documento {tercero.documento}, no {documento}.")
        return tercero


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8"))
        return str(data.get("error") or data)[:300]
    except Exception:
        return exc.reason or ""
