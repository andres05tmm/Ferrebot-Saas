"""E2 RED — MatiasClient por empresa (parsers puros + orquestación httpx, cero red).

Pin del contrato MATIAS (`docs/facturacion-matias-extract.md` §2/§5/§10/§11): extracción de token,
parse de emisión con FAD06, parse de ciudades; y la orquestación PEREZOSA (auth con caché, `/invoice`,
`/cities`) verificada con `httpx.MockTransport` — sin tocar la red ni al importar ni al construir.

En RED todos fallan: parsers lanzan NotImplementedError y los métodos async también.
"""
from datetime import datetime, timezone

import httpx
import pytest

from modules.facturacion.matias_client import (
    CUFE_MIN_LEN,
    MatiasClient,
    MatiasCredenciales,
    _extraer_token,
    _parsear_ciudades,
    _parsear_emision,
)

_CRED = MatiasCredenciales(email="bot@empresa.co", password="secreto", base_url="https://matias.test/api")
_CUFE_OK = "a" * CUFE_MIN_LEN


# --- parsers puros (§2/§5/§10) ----------------------------------------------

def test_extraer_token_variantes():
    ahora = 1_000.0
    for data in ({"token": "T"}, {"access_token": "T"},
                 {"data": {"token": "T"}}, {"data": {"access_token": "T"}}):
        tok, _exp = _extraer_token(data, ahora=ahora)
        assert tok == "T"
    iso = "2026-06-04T10:30:00Z"                       # "Z" → +00:00
    esperado = datetime(2026, 6, 4, 10, 30, tzinfo=timezone.utc).timestamp()
    _t, exp = _extraer_token({"token": "T", "expires_at": iso}, ahora=ahora)
    assert exp == esperado
    _t, exp = _extraer_token({"token": "T", "expires_in": 100}, ahora=ahora)
    assert exp == ahora + 100
    _t, exp = _extraer_token({"token": "T"}, ahora=ahora)
    assert exp == ahora + 86_400                       # default si no hay pista
    with pytest.raises(ValueError):
        _extraer_token({"foo": "bar"}, ahora=ahora)    # token ausente


def test_parsear_emision_exito():
    res = _parsear_emision({"success": True, "XmlDocumentKey": _CUFE_OK})
    assert res.ok is True and res.cufe == _CUFE_OK
    assert res.categoria == "aceptada"


def test_parsear_emision_fad06():
    corto = _parsear_emision({"success": True, "XmlDocumentKey": "abc"})   # <40 chars
    assert corto.ok is False and "CUFE inválido" in corto.error_msg
    sin = _parsear_emision({"success": True})                              # sin CUFE
    assert sin.ok is False and "CUFE inválido" in sin.error_msg
    assert corto.categoria == "error" and sin.categoria == "error"


def test_parsear_emision_rechazo():
    res = _parsear_emision(
        {"success": False, "message": "Rechazado", "errors": {"customer.dni": "requerido"}}
    )
    assert res.ok is False
    assert "Rechazado" in res.error_msg and "customer.dni: requerido" in res.error_msg
    assert res.categoria == "rechazada"


def test_parsear_ciudades_variantes():
    d1 = {"dataRecords": {"data": [{"code": "5001", "id": "149"}]}}        # forma dataRecords.data
    assert _parsear_ciudades(d1) == {5001: "149"}
    d2 = {"data": [{"dane_code": "11001", "id": "1"},                      # code desde dane_code
                   {"municipality_code": "76001", "id": "2"}]}            # y municipality_code
    assert _parsear_ciudades(d2) == {11001: "1", 76001: "2"}
    assert _parsear_ciudades({}) == {}                                    # vacío → {}
    d3 = {"data": [{"code": "XX", "id": "9"},                             # code no numérico → salta
                   {"code": "8001"},                                      # sin id → salta
                   {"code": "5001", "id": "149"}]}
    assert _parsear_ciudades(d3) == {5001: "149"}


# --- orquestación con httpx.MockTransport (cero red) -------------------------

class _Handler:
    """Handler de MockTransport: respuestas canned por endpoint + traza de paths (sin red real)."""

    def __init__(self, *, token="TKN", login=None, invoice=None, cities=None):
        self._login = login if login is not None else {"token": token, "expires_in": 3600}
        self._invoice = invoice
        self._cities = cities if cities is not None else {"data": []}
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path.endswith("/auth/login"):
            return httpx.Response(200, json=self._login)
        if path.endswith("/invoice"):
            return httpx.Response(200, json=self._invoice)
        if path.endswith("/cities"):
            return httpx.Response(200, json=self._cities)
        return httpx.Response(404, json={})


def _client(handler: _Handler) -> MatiasClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_CRED.base_url)
    return MatiasClient(_CRED, client=http)


def _cuenta(handler: _Handler, sufijo: str) -> int:
    return sum(p.endswith(sufijo) for p in handler.paths)


async def test_token_perezoso_y_cache():
    handler = _Handler(token="JWT-1")
    cli = _client(handler)
    assert handler.paths == []                         # construir NO toca la red (CR-1)
    t1 = await cli._token()
    t2 = await cli._token()
    assert t1 == t2 == "JWT-1"
    assert _cuenta(handler, "/auth/login") == 1        # el segundo _token reusa la caché


async def test_emitir_factura_exito():
    handler = _Handler(invoice={"success": True, "XmlDocumentKey": _CUFE_OK})
    res = await _client(handler).emitir_factura({"document_number": "1024"})
    assert res.ok is True and res.cufe == _CUFE_OK
    assert _cuenta(handler, "/invoice") == 1


async def test_emitir_factura_rechazo():
    handler = _Handler(invoice={"success": False, "message": "Rechazado", "errors": {"x": "y"}})
    res = await _client(handler).emitir_factura({"document_number": "1024"})
    assert res.ok is False and "Rechazado" in res.error_msg


async def test_city_id_carga_y_cachea():
    handler = _Handler(cities={"dataRecords": {"data": [{"code": "5001", "id": "149"}]}})
    cli = _client(handler)
    assert await cli.city_id("5001") == "149"
    assert await cli.city_id(5001) == "149"            # segunda vez: sin recargar
    assert await cli.city_id("9999") is None           # desconocido → None
    assert _cuenta(handler, "/cities") == 1            # se cargó una sola vez
