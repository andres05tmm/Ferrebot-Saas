"""Webhook del push de Gmail (modules/bancos/gmail/webhook.py) — lógica pura, sin FastAPI.

INVARIANTE de aislamiento (TDD): un token desconocido NO resuelve empresa → 404 fail-closed (jamás
escribe en un tenant que no le corresponde). Token válido → encola con la empresa correcta.
"""
import base64
import json

import pytest

from modules.bancos.gmail.webhook import AccionPush, WebhookGmailDeps, manejar_push


def _push_body(history_id: str) -> bytes:
    interno = base64.urlsafe_b64encode(
        json.dumps({"emailAddress": "x@y.com", "historyId": history_id}).encode()).decode()
    return json.dumps({"message": {"data": interno}}).encode()


def _deps(*, empresa_por_token: dict[str, int]):
    encolados = []

    async def resolver(token):
        return empresa_por_token.get(token)

    async def encolar(empresa_id, history_id):
        encolados.append((empresa_id, history_id))

    return WebhookGmailDeps(resolver=resolver, encolar=encolar), encolados


@pytest.mark.anyio
async def test_token_desconocido_404_no_encola():
    deps, encolados = _deps(empresa_por_token={"tok-A": 1})
    res = await manejar_push(token="tok-DESCONOCIDO", cuerpo=_push_body("999"), deps=deps)
    assert res.accion == AccionPush.NO_REGISTRADO and res.status == 404
    assert encolados == []              # aislamiento: no se encoló nada


@pytest.mark.anyio
async def test_token_valido_encola_empresa_correcta_con_history():
    deps, encolados = _deps(empresa_por_token={"tok-A": 7})
    res = await manejar_push(token="tok-A", cuerpo=_push_body("12345"), deps=deps)
    assert res.accion == AccionPush.ENCOLADO and res.status == 200
    assert encolados == [(7, "12345")]


@pytest.mark.anyio
async def test_envelope_invalido_igual_encola_sin_history():
    # Un cuerpo que no es el envelope esperado no debe fallar el push (Pub/Sub reintentaría en bucle).
    deps, encolados = _deps(empresa_por_token={"tok-A": 7})
    res = await manejar_push(token="tok-A", cuerpo=b"no-es-json", deps=deps)
    assert res.status == 200
    assert encolados == [(7, None)]
