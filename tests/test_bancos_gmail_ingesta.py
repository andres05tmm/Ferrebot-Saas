"""Ingesta Bancolombia (modules/bancos/gmail/ingesta.py) — invariante de idempotencia.

INVARIANTE (TDD, `.claude/rules/testing.md`): el MISMO `gmail_message_id` procesado dos veces deja
UNA sola fila y notifica UNA sola vez (dedup por `gmail_message_id`). Se corre contra una base
efímera real (fixture `tenant`) con un cliente Gmail falso que sirve un mensaje fijo.
"""
import base64

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.bancos.gmail.ingesta import procesar_push
from modules.bancos.repository import SqlBancosRepository

_BODY = ("recibiste un pago de PEDRO PEREZ por $25.000 en tu cuenta *3891 "
         "el 01/07/2026 a las 10:15. Con codigo QR es facil.")


class _FakeCliente:
    """GmailCliente falso: history devuelve un ID fijo; headers/mensaje sirven un email Bancolombia."""

    def __init__(self, message_id="msg-1"):
        self._mid = message_id
        self.refresh_token_rotado = None

    async def ids_desde_history(self, history_id):
        return [self._mid]

    async def headers(self, message_id):
        return [{"name": "From", "value": "notificaciones@bancolombia.com.co"},
                {"name": "Subject", "value": "Recibiste una transferencia"}]

    async def mensaje_completo(self, message_id):
        data = base64.urlsafe_b64encode(_BODY.encode()).decode().rstrip("=")
        return {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": data}}]}}


@pytest.mark.anyio
async def test_ingesta_idempotente_mismo_message_id(tenant):
    """Dos corridas con el mismo message_id → 1 fila, 1 notificación."""
    notificados = []

    async def notificar(texto):
        notificados.append(texto)

    async with AsyncSession(tenant.engine) as s:
        repo = SqlBancosRepository(s)
        r1 = await procesar_push(cliente=_FakeCliente(), repo=repo, last_history_id="100",
                                 notificar=notificar, history_id_push="200")
        await s.commit()
        r2 = await procesar_push(cliente=_FakeCliente(), repo=repo, last_history_id="200",
                                 notificar=notificar, history_id_push="300")
        await s.commit()
        total = (await s.execute(
            __import__("sqlalchemy").text("SELECT count(*) FROM bancolombia_transferencias"))).scalar_one()

    assert r1.insertados == 1 and r2.insertados == 0   # la segunda no re-inserta
    assert total == 1                                  # una sola fila
    assert len(notificados) == 1                       # una sola notificación


@pytest.mark.anyio
async def test_ingesta_persiste_credito_y_monto(tenant):
    async with AsyncSession(tenant.engine) as s:
        await procesar_push(cliente=_FakeCliente(), repo=SqlBancosRepository(s),
                            last_history_id="100", notificar=lambda t: _noop(), history_id_push="200")
        await s.commit()
        fila = (await s.execute(__import__("sqlalchemy").text(
            "SELECT monto, naturaleza, remitente, notificado FROM bancolombia_transferencias"))).first()
    assert fila[0] == 25000 and fila[1] == "credito"
    assert "PEDRO" in fila[2] and fila[3] is True


@pytest.mark.anyio
async def test_ingesta_sin_history_previo_no_procesa(tenant):
    """Primer push tras activar el watch (sin last_history_id): adopta el punto y sale sin leer."""
    async with AsyncSession(tenant.engine) as s:
        r = await procesar_push(cliente=_FakeCliente(), repo=SqlBancosRepository(s),
                               last_history_id=None, notificar=lambda t: _noop(), history_id_push="500")
    assert r.insertados == 0 and r.nuevo_history_id == "500"


async def _noop():
    return None
