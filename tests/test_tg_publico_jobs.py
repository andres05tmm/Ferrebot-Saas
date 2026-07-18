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



async def _sin_qr(tenant, telefono, chat_id):
    return False

async def test_job_atiende_via_agente_con_identidad_del_payload(monkeypatch):
    atendidos = []
    tenant = _tenant(7)

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append((mensaje, tnt))

    async def _resolver(tid):
        return tenant if tid == 7 else None

    import apps.tg_publico.jobs as jobs
    monkeypatch.setattr(jobs, "_enviar_qr_pago", _sin_qr)
    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "quiero una carne asada", 42)
    assert res == "atendido"
    mensaje, tnt = atendidos[0]
    assert tnt.id == 7
    assert mensaje.telefono == TEL              # "tg:{chat_id}" — identidad del payload
    assert mensaje.phone_number_id == "7"       # transporta el tenant_id para el sender
    assert mensaje.texto == "quiero una carne asada"


async def test_job_menu_con_foto_responde_corto_sin_agente(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    fotos, atendidos, respuestas = [], [], []

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append(mensaje.texto)

    async def _resolver(tid):
        return tenant

    async def _fake_foto(tenant_id, chat_id):
        fotos.append((tenant_id, chat_id))
        return True

    async def _fake_responder(tenant_id, telefono, texto):
        respuestas.append(texto)

    async def _fake_persistir(tnt, telefono, entrante, saliente):
        pass

    monkeypatch.setattr(jobs, "_enviar_qr_pago", _sin_qr)
    monkeypatch.setattr(jobs, "_enviar_foto_menu", _fake_foto)
    monkeypatch.setattr(jobs, "_responder", _fake_responder)
    monkeypatch.setattr(jobs, "_persistir_intercambio", _fake_persistir)
    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "me pasas el menú porfa?", 1)
    assert res == "menu_foto"
    assert fotos == [(7, CHAT_ID)]              # pidió menú → foto
    assert respuestas == [jobs._MSG_MENU]       # respuesta corta fija
    assert atendidos == []                      # la imagen ES el menú: el agente NO corre
    await atender_mensaje_tg(ctx, 7, CHAT_ID, "quiero una carne asada", 2)
    assert fotos == [(7, CHAT_ID)]              # no pidió menú → sin foto nueva
    assert atendidos == ["quiero una carne asada"]   # el agente sí corre para el pedido


async def test_job_menu_sin_foto_cae_al_agente(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    atendidos = []

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append(mensaje.texto)

    async def _resolver(tid):
        return tenant

    async def _sin_foto(tenant_id, chat_id):
        return False    # tenant sin menu_foto_path configurada

    monkeypatch.setattr(jobs, "_enviar_qr_pago", _sin_qr)
    monkeypatch.setattr(jobs, "_enviar_foto_menu", _sin_foto)
    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "muéstrame el menú", 3)
    assert res == "atendido"
    assert atendidos == ["muéstrame el menú"]   # fallback: el menú sale en texto por el agente


async def test_job_foto_fallida_no_tumba_el_turno(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    atendidos = []

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append(mensaje.texto)

    async def _resolver(tid):
        return tenant

    async def _boom(tenant_id, chat_id):
        raise RuntimeError("telegram caído")

    monkeypatch.setattr(jobs, "_enviar_qr_pago", _sin_qr)
    monkeypatch.setattr(jobs, "_enviar_foto_menu", _boom)
    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "muéstrame la carta", 3)
    assert res == "atendido"                    # la foto es cortesía: el turno sigue
    assert atendidos == ["muéstrame la carta"]


async def test_job_manda_qr_tras_el_turno_y_un_fallo_no_lo_tumba(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    qr_llamadas, atendidos = [], []

    class _FakeAgente:
        async def atender(self, mensaje, tnt):
            atendidos.append(mensaje.texto)

    async def _resolver(tid):
        return tenant

    async def _fake_qr(tnt, telefono, chat_id):
        qr_llamadas.append((tnt.id, telefono, chat_id))
        return True

    monkeypatch.setattr(jobs, "_enviar_qr_pago", _fake_qr)
    ctx = {"resolver_tenant": _resolver, "tg_agente": _FakeAgente()}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "confirma mi pedido porfa", 5)
    assert res == "atendido"
    assert qr_llamadas == [(7, TEL, CHAT_ID)]   # el hook corre DESPUÉS del turno del agente

    async def _qr_boom(tnt, telefono, chat_id):
        raise RuntimeError("qr caído")

    monkeypatch.setattr(jobs, "_enviar_qr_pago", _qr_boom)
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "otro mensaje", 6)
    assert res == "atendido"                    # el QR es cortesía: nunca tumba el turno
    assert atendidos == ["confirma mi pedido porfa", "otro mensaje"]


async def test_job_sin_tenant_no_atiende():
    async def _resolver(tid):
        return None

    res = await atender_mensaje_tg({"resolver_tenant": _resolver, "tg_agente": None}, 9, CHAT_ID, "hola", 1)
    assert res == "sin_tenant"


# --- sender -----------------------------------------------------------------
class _FakeNotificador:
    def __init__(self, *, bot_token, rechaza_html=False):
        self.bot_token = bot_token
        self.enviados = []
        self._rechaza_html = rechaza_html

    async def responder(self, chat_id, texto, *, teclado=None, parse_mode=None):
        from apps.bot.telegram import TelegramError

        if self._rechaza_html and parse_mode == "HTML":
            raise TelegramError("can't parse entities")
        self.enviados.append((chat_id, texto, parse_mode))


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
    assert creados[0].enviados == [(CHAT_ID, "listo", "HTML")]


def test_telegramify_negrita_y_escape():
    from apps.tg_publico.sender import telegramify

    assert telegramify("Bienvenido a *Siriuss*") == "Bienvenido a <b>Siriuss</b>"
    assert telegramify("total *$30.000* <ya>") == "total <b>$30.000</b> &lt;ya&gt;"
    # Un asterisco suelto o multilínea no se toca (nada de entidades a medio balancear).
    assert telegramify("2 * 3 = 6") == "2 * 3 = 6"


async def test_sender_html_rechazado_cae_a_plano():
    n = _FakeNotificador(bot_token="tok", rechaza_html=True)

    async def resolver_token(tenant_id):
        return "tok"

    sender = TelegramPublicoSender("master", resolver_token=resolver_token,
                                   notificador_factory=lambda *, bot_token: n)
    await sender.enviar_texto(phone_number_id="7", to=TEL, texto="*hola*")
    assert n.enviados == [(CHAT_ID, "*hola*", None)]   # fallback: el texto original, en plano


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
