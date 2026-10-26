"""Terceros API client against a local fake server (same JSON shape as the real API)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.terceros_api import TerceroApiError, TercerosApi

KEY = "test-key"
INFO = {
    "identificacion": {"tipoDocumento": "CEDULA DE CIUDADANIA", "documento": "22222222",
                       "tipoPersona": "NATURAL", "nombre": "JUAN CARLOS RAMIREZ"},
    "residenciaEntrega": {"pais": "Colombia", "departamento": "Valle del Cauca", "ciudad": "CALI",
                          "direccion": "CL 10  # 20-30", "codigoPostal": "76001", "email": "jhon@x.com",
                          "telefono": "3001234567", "emailEntrega": "informacionalproveedor@elogia.com.co",
                          "confirmarEmailEntrega": "informacionalproveedor@elogia.com.co"},
    "direccionFiscal": {},
    "obligacionesAplicables": [{"codigo": "R-99-PN", "descripcion": "No aplica – Otros"}],
    "fuente": {"ciudadId": "123", "ciudadDane": "76001000"},
}
RAW = {"tercero": {"nombre1": "JUAN", "nombre2": "CARLOS", "apellido1": "RAMIREZ", "apellido2": ""}}


CALLS: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        CALLS.append(self.path)
        if self.headers.get("x-api-key") != KEY:
            return self._send(401, {"error": "Invalid or missing x-api-key."})
        if self.path == "/api/terceros/22222222":
            return self._send(200, INFO)
        if self.path == "/api/terceros/22222222/raw":
            return self._send(200, RAW)
        if self.path == "/api/terceros/11111111":
            return self._send(409, {"error": "Several", "details": ["a", "b"]})
        return self._send(404, {"error": "Tercero not found."})

    def _send(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_get_tercero_with_address_and_names(base_url):
    t = TercerosApi(base_url, KEY, timeout=5).get("22222222", with_names=True)
    assert (t.departamento, t.ciudad, t.codigo_postal, t.ciudad_dane) == ("Valle del Cauca", "CALI", "76001", "76001000")
    assert t.direccion == "CL 10 # 20-30" and t.email_entrega.startswith("informacionalproveedor")
    assert (t.nombre1, t.nombre2, t.apellido1) == ("JUAN", "CARLOS", "RAMIREZ")
    assert t.obligaciones == ["R-99-PN"] and t.missing_address() == []


def test_answers_are_cached_and_prefetch_warms_the_cache(base_url):
    api = TercerosApi(base_url, KEY, timeout=5)
    api.prefetch(["22222222", "22222222", "1055555555"])
    for _ in range(50):
        if len(api._cache) == 2:
            break
        time.sleep(0.1)
    CALLS.clear()
    assert api.get("22222222", with_names=True).nombre1 == "JUAN"
    assert api.get("22222222").documento == "22222222"  # the full answer serves the short one
    assert api.get("1055555555", with_names=True) is None
    assert CALLS == []


def test_not_found_is_none(base_url):
    assert TercerosApi(base_url, KEY, timeout=5).get("1055555555") is None


def test_bad_key_and_several_matches(base_url):
    with pytest.raises(TerceroApiError, match="401"):
        TercerosApi(base_url, "wrong", timeout=5).get("22222222")
    with pytest.raises(TerceroApiError, match="Varios terceros"):
        TercerosApi(base_url, KEY, timeout=5).get("11111111")


def test_invalid_document_is_rejected_locally():
    with pytest.raises(TerceroApiError, match="inválido"):
        TercerosApi("http://127.0.0.1:9", KEY).get("12/34")


def test_missing_key_message(monkeypatch):
    monkeypatch.delenv("TERCEROS_API_KEY", raising=False)
    import app.config as cfg

    monkeypatch.setattr(cfg, "get_credential", lambda name: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(TerceroApiError, match="Configuración"):
        TercerosApi("http://127.0.0.1:9").get("22222222")
