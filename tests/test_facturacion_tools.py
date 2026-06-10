"""F2.1.4 — tools `set_config` y `registrar_webhook_matias`: lógica pura/aislada (sin red ni control DB).

El `set_config` real (UPSERT en control DB) reusa el molde probado de `set_feature`; aquí se cubre la
guarda anti-secreto. El registro del webhook se prueba con `httpx.MockTransport` (cero red)."""
import httpx
import pytest

from modules.facturacion.matias_client import (
    MatiasClient,
    MatiasCredenciales,
    _parsear_secret_webhook,
)
from tools.set_config import _es_clave_secreto

_CRED = MatiasCredenciales(email="bot@e.co", password="x", base_url="https://matias.test/api/ubl2.1")


# --- set_config: guarda anti-secreto -----------------------------------------

def test_es_clave_secreto():
    assert _es_clave_secreto("matias_password") is True
    assert _es_clave_secreto("matias_webhook_secret") is True
    assert _es_clave_secreto("anthropic_api_key") is True
    assert _es_clave_secreto("matias_resolution_pos") is False
    assert _es_clave_secreto("pos_terminal") is False


def test_set_config_rechaza_secreto():
    from tools.set_config import set_config
    with pytest.raises(ValueError, match="parece un secreto"):
        set_config("pr", "matias_password", "hunter2")    # falla ANTES de tocar la BD


# --- parser del secret de registro -------------------------------------------

def test_parsear_secret_webhook_variantes():
    assert _parsear_secret_webhook({"secret": "S1"}) == "S1"
    assert _parsear_secret_webhook({"signing_secret": "S2"}) == "S2"
    assert _parsear_secret_webhook({"data": {"webhook_secret": "S3"}}) == "S3"
    with pytest.raises(ValueError):
        _parsear_secret_webhook({"foo": "bar"})           # sin secret → error


# --- registrar_webhook del cliente (MockTransport) ---------------------------

async def test_registrar_webhook_devuelve_secret():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "TKN", "expires_in": 3600})
        if request.url.path.endswith("/ubl2.1/webhooks"):
            return httpx.Response(200, json={"secret": "wh-secret-xyz"})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_CRED.base_url)
    cli = MatiasClient(_CRED, client=http)
    secret = await cli.registrar_webhook(
        "https://app/webhooks/matias/tok",
        events=["document.accepted"],
        registro_url="https://matias.test/ubl2.1/webhooks",
    )
    assert secret == "wh-secret-xyz"
    assert any(p.endswith("/ubl2.1/webhooks") for p in paths)
