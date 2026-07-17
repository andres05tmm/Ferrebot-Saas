"""Job del canal Telegram (`atender_mensaje_tg`) y el sender (`TelegramPublicoSender`) con fakes.

El job resuelve el tenant y delega en el `AgenteWa` (seam `ctx["tg_agente"]`); el sender traduce
`phone_number_id`=tenant_id + `to`="tg:{chat_id}" al `responder(chat_id, texto)` del notificador, con
el token resuelto por tenant y cacheado. Espeja los tests de job/sender del canal WhatsApp.
"""
import pytest

from apps.tg_publico.jobs import atender_mensaje_tg
from apps.tg_publico.sender import TelegramPublicoSender, TokenTgFaltante
from core.tenancy.context import ResolvedTenant

CHAT_ID = 987654321
TEL = f"tg:{CHAT_ID}"


def _tenant(id: int = 7) -> ResolvedTenant:
    return ResolvedTenant(id=id, slug="sirius", nombre="Sirius", estado="activa",
                          db_name="d", connection_url="postgresql://x/y")


async def test_job_atiende_via_agente_con_identidad_del_payload():
    atendidos = []
    tenant = _tenant(7)

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append((mensaje, tnt))

    async def _resolver(tid):
        return tenant if tid == 7 else None

    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "quiero una carne asada", 42)
    assert res == "atendido"
    mensaje, tnt = atendidos[0]
    assert tnt.id == 7
    assert mensaje.telefono == TEL              # "tg:{chat_id}" — identidad del payload
    assert mensaje.phone_number_id == "7"       # transporta el tenant_id para el sender
    assert mensaje.texto == "quiero una carne asada"


async def test_job_sin_tenant_no_atiende():
    async def _resolver(tid):
        return None

    res = await atender_mensaje_tg({"resolver_tenant": _resolver, "tg_agente": None}, 9, CHAT_ID, "hola", 1)
    assert res == "sin_tenant"


# --- sender -----------------------------------------------------------------
class _FakeNotificador:
    def __init__(self, *, bot_token):
        self.bot_token = bot_token
        self.enviados = []

    async def responder(self, chat_id, texto, *, teclado=None):
        self.enviados.append((chat_id, texto))


async def test_sender_traduce_to_y_phone_number_id():
    creados = []

    def factory(*, bot_token):
        n = _FakeNotificador(bot_token=bot_token)
        creados.append(n)
        return n

    async def resolver_token(tenant_id):
        return f"token-{tenant_id}"

    sender = TelegramPublicoSender("master", resolver_token=resolver_token, notificador_factory=factory)
    await sender.enviar_texto(phone_number_id="7", to=TEL, texto="listo")
    assert creados[0].bot_token == "token-7"
    assert creados[0].enviados == [(CHAT_ID, "listo")]


async def test_sender_cachea_notificador_por_tenant():
    llamadas = {"n": 0}

    async def resolver_token(tenant_id):
        llamadas["n"] += 1
        return "tok"

    sender = TelegramPublicoSender("master", resolver_token=resolver_token,
                                   notificador_factory=lambda *, bot_token: _FakeNotificador(bot_token=bot_token))
    await sender.enviar_texto(phone_number_id="7", to=TEL, texto="a")
    await sender.enviar_texto(phone_number_id="7", to=TEL, texto="b")
    assert llamadas["n"] == 1                   # token leído una sola vez (cacheado)


async def test_sender_sin_token_lanza():
    async def resolver_token(tenant_id):
        return None

    sender = TelegramPublicoSender("master", resolver_token=resolver_token)
    with pytest.raises(TokenTgFaltante):
        await sender.enviar_texto(phone_number_id="7", to=TEL, texto="x")
