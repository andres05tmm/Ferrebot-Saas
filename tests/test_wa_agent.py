"""Bucle del agente de WhatsApp (`apps/wa/agent.py`): bucle LLM, memoria, e2e y fallback.

- `correr_bucle` con LLM mockeado: despacha tool_calls, realimenta el tool_result, produce texto final.
- `MemoriaWa`: persiste/recupera el historial por (tenant, teléfono) en Redis (cliente fake).
- e2e: una conversación real (LLM scripteado) agenda una cita → fila en BD, con el teléfono DEL
  CONTEXTO (no de los args del modelo), y la respuesta sale por el sender.
- Fallback elegante si el LLM falla.
"""
from datetime import datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.wa.agent import AgenteWa, MemoriaWa, correr_bucle, construir_system, whatsappify
from apps.wa.kapso import MensajeWa
from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import COLOMBIA_TZ, today_co
from core.llm.base import LLMResponse, Message, ToolCall
from core.llm.factory import LLMResuelto, Turno
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)

TEL = "573001112233"
PNID = "123456789012345"


def _futuro(hora: int = 10, minuto: int = 0, dias: int = 3) -> datetime:
    return datetime.combine(today_co() + timedelta(days=dias), time(hora, minuto), tzinfo=COLOMBIA_TZ)


def _tc(herramienta: str, **args) -> ToolCall:
    return ToolCall(id=f"c-{herramienta}", name=herramienta, arguments=args)


class _ScriptedProvider:
    """Proveedor LLM falso: devuelve respuestas en orden y registra lo que se le pasó."""

    nombre = "fake"
    api_key = "x"

    def __init__(self, respuestas: list[LLMResponse]) -> None:
        self._respuestas = list(respuestas)
        self.generaciones: list[dict] = []

    async def generate(self, *, messages, tools, model, system=None, **kw) -> LLMResponse:
        self.generaciones.append({"messages": list(messages), "tools": tools, "system": system})
        return self._respuestas.pop(0)


def _llm(provider) -> LLMResuelto:
    return LLMResuelto(provider=provider, model="modelo-x", provider_nombre="fake")


class _FakeRedis:
    def __init__(self): self.store = {}
    async def get(self, k): return self.store.get(k)
    async def set(self, k, v, ex=None): self.store[k] = v


class _FakeSender:
    def __init__(self): self.envios = []
    async def enviar_texto(self, *, phone_number_id, to, texto):
        self.envios.append((phone_number_id, to, texto))


# --- system prompt ----------------------------------------------------------
def test_system_prompt_es_especializado_y_usa_persona():
    base = construir_system(None)
    assert "citas" in base and "ÚNICO" in base               # especializado (regla de Meta)
    con_persona = construir_system("Hablas como la barbería El Navaja, relajado y costeño.")
    assert "El Navaja" in con_persona


def test_system_prompt_incluye_la_fecha_de_hoy_colombia():
    from core.config.timezone import now_co
    ahora = now_co()
    sys = construir_system(None)
    assert "Hoy es" in sys and "hora de Colombia" in sys
    assert f"{ahora.day} de" in sys and str(ahora.year) in sys   # ancla la fecha actual
    assert "lo más pronto posible" in sys                        # guía las fechas relativas


def test_whatsappify_negrita_encabezados_separadores_links():
    entrada = "### Servicios\n**Limpieza** y ***Blanqueamiento***\n---\nVer [aquí](https://x.co)"
    out = whatsappify(entrada)
    assert "*Limpieza*" in out and "*Blanqueamiento*" in out
    assert "**" not in out                 # negrita Markdown → un solo asterisco
    assert "###" not in out and "Servicios" in out   # encabezado: se quita el marcador, queda el texto
    assert "---" not in out                # separador eliminado
    assert "aquí: https://x.co" in out     # link → "texto: url"


# --- bucle con LLM mockeado -------------------------------------------------
async def test_bucle_despacha_tool_y_realimenta_resultado():
    provider = _ScriptedProvider([
        LLMResponse(text=None, tool_calls=[_tc("listar_servicios")]),  # 1ª: pide herramienta
        LLMResponse(text="Tenemos Limpieza dental.", tool_calls=[]),    # 2ª: responde texto
    ])
    llamadas = []

    async def fake_ejecutar(call, ctx, deps):
        llamadas.append(call.name)
        return Resultado(data={"servicios": []}, resumen="Limpieza dental (40 min).")

    texto = await correr_bucle(
        proveedor=_llm(provider), system="s", tools=[], ctx=_ctx(), deps=None,
        historial=[], texto="¿qué servicios hay?", ejecutar=fake_ejecutar,
    )
    assert texto == "Tenemos Limpieza dental."
    assert llamadas == ["listar_servicios"]
    # La 2ª generación vio el tool_result realimentado.
    msgs2 = provider.generaciones[1]["messages"]
    assert any(m.role == "tool" and m.name == "listar_servicios" for m in msgs2)


async def test_bucle_realimenta_error_recuperable_al_modelo():
    provider = _ScriptedProvider([
        LLMResponse(text=None, tool_calls=[_tc("agendar_cita")]),
        LLMResponse(text="Ese horario está ocupado, ¿te ofrezco otro?", tool_calls=[]),
    ])

    async def fake_ejecutar(call, ctx, deps):
        return ErrorTool("cupo_no_disponible", "Alternativas: vie 14:00.", recuperable=True)

    texto = await correr_bucle(
        proveedor=_llm(provider), system="s", tools=[], ctx=_ctx(), deps=None,
        historial=[], texto="agéndame", ejecutar=fake_ejecutar,
    )
    assert texto == "Ese horario está ocupado, ¿te ofrezco otro?"
    # El modelo recibió el envelope de error (ok:false) para repreguntar.
    tool_msg = [m for m in provider.generaciones[1]["messages"] if m.role == "tool"][0]
    assert '"ok": false' in tool_msg.content and "Alternativas" in tool_msg.content


async def test_bucle_corta_en_el_tope_de_iteraciones():
    # Siempre pide herramienta: al agotar max_iters hace una generación final SIN tools.
    provider = _ScriptedProvider([
        LLMResponse(text=None, tool_calls=[_tc("listar_servicios")]),
        LLMResponse(text=None, tool_calls=[_tc("listar_servicios")]),
        LLMResponse(text="Cierro con texto.", tool_calls=[]),  # generación final sin tools
    ])

    async def fake_ejecutar(call, ctx, deps):
        return Resultado(data={}, resumen="ok")

    texto = await correr_bucle(
        proveedor=_llm(provider), system="s", tools=[], ctx=_ctx(), deps=None,
        historial=[], texto="hola", ejecutar=fake_ejecutar, max_iters=2,
    )
    assert texto == "Cierro con texto."
    assert provider.generaciones[-1]["tools"] == []   # la generación de cierre va sin herramientas


def _ctx() -> Contexto:
    return Contexto(tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
                    cliente_telefono=TEL, capacidades=frozenset({"pack_agenda"}))


# --- memoria ----------------------------------------------------------------
async def test_memoria_persiste_y_recupera_turnos():
    mem = MemoriaWa(url="x", client=_FakeRedis())
    assert await mem.cargar(1, TEL) == []

    await mem.guardar(1, TEL, [], "hola", "¿Qué servicio quieres?")
    hist = await mem.cargar(1, TEL)
    assert [(m.role, m.content) for m in hist] == [
        ("user", "hola"), ("assistant", "¿Qué servicio quieres?"),
    ]
    # Segundo turno: arrastra el historial previo.
    await mem.guardar(1, TEL, hist, "una limpieza", "¿Para cuándo?")
    assert len(await mem.cargar(1, TEL)) == 4


async def test_memoria_recorta_a_max_turnos():
    mem = MemoriaWa(url="x", client=_FakeRedis(), max_turnos=1)
    previo = [Message(role="user", content="a"), Message(role="assistant", content="b")]
    await mem.guardar(1, TEL, previo, "c", "d")
    hist = await mem.cargar(1, TEL)
    assert [(m.role, m.content) for m in hist] == [("user", "c"), ("assistant", "d")]  # solo el último


async def test_memoria_aisla_por_telefono():
    redis = _FakeRedis()
    mem = MemoriaWa(url="x", client=redis)
    await mem.guardar(1, "573001110000", [], "soy A", "ok A")
    assert await mem.cargar(1, "573009998888") == []  # otro teléfono: sin historial


# --- e2e: una conversación real agenda una cita -----------------------------
def _abrir_factory(engine):
    async def _abrir(tenant):
        async with AsyncSession(engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise
    return _abrir


def _tenant_resuelto():
    from core.tenancy.context import ResolvedTenant
    return ResolvedTenant(id=1, slug="clinica", estado="activa", db_name="d",
                          connection_url="postgresql://x/y")


async def _seed(engine) -> tuple[int, int]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        repo = SqlAgendaRepository(s)
        serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza dental", duracion_min=30, precio="80000"))
        rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
        await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
        for dia in range(7):
            await repo.crear_disponibilidad(
                DisponibilidadCrear(recurso_id=rec.id, dia_semana=dia, hora_inicio=time(8), hora_fin=time(18))
            )
        await repo.guardar_config(
            AgendaConfigCrear(modo_confirmacion="auto", anticipacion_minima_min=0,
                              ventana_maxima_dias=60, intervalo_slots_min=30,
                              persona="Cordial y breve.")
        )
        await s.commit()
        return serv.id, rec.id


async def test_e2e_conversacion_agenda_cita_real(tenant):
    serv, rec = await _seed(tenant.engine)
    slot = _futuro(hora=10)

    # El LLM scripteado conduce la reserva: lista → consulta → agenda → confirma.
    # En agendar mete un cliente_telefono MALICIOSO en los args: debe ignorarse (sale del Contexto).
    provider = _ScriptedProvider([
        LLMResponse(text=None, tool_calls=[_tc("listar_servicios")]),
        LLMResponse(text=None, tool_calls=[_tc("consultar_disponibilidad", servicio_id=serv,
                                               desde=slot.date().isoformat(), hasta=slot.date().isoformat())]),
        LLMResponse(text=None, tool_calls=[_tc("agendar_cita", servicio_id=serv, inicio=slot.isoformat(),
                                               nombre="Andrés", cliente_telefono="570000000000")]),
        LLMResponse(text="Listo Andrés ✅ Tu cita quedó agendada.", tool_calls=[]),
    ])
    sender = _FakeSender()
    agente = AgenteWa(
        abrir_tenant=_abrir_factory(tenant.engine),
        resolver_llm=lambda tid, turno: _coro(_llm(provider)),
        capacidades=lambda tid: _coro(frozenset({"pack_agenda"})),
        memoria=MemoriaWa(url="x", client=_FakeRedis()),
        sender=sender,
    )
    mensaje = MensajeWa(message_id="wamid.1", telefono=TEL, phone_number_id=PNID,
                        texto="Quiero una limpieza el viernes a nombre de Andrés")

    texto = await agente.atender(mensaje, _tenant_resuelto())

    assert texto == "Listo Andrés ✅ Tu cita quedó agendada."
    assert sender.envios == [(PNID, TEL, texto)]   # responde al teléfono del payload
    # La cita quedó en la BD con el teléfono DEL CONTEXTO (no el malicioso de los args).
    async with AsyncSession(tenant.engine) as s:
        fila = (await s.execute(
            text("SELECT cliente_nombre, cliente_telefono, estado, servicio_id FROM citas")
        )).one()
    assert fila.cliente_telefono == TEL           # del Contexto, no '570000000000'
    assert fila.cliente_nombre == "Andrés"
    assert fila.estado == "confirmada" and fila.servicio_id == serv


async def test_envia_texto_whatsappificado(tenant):
    await _seed(tenant.engine)
    provider = _ScriptedProvider([LLMResponse(text="**Hola** Andrés\n### Cita\nlista", tool_calls=[])])
    sender = _FakeSender()
    agente = AgenteWa(
        abrir_tenant=_abrir_factory(tenant.engine),
        resolver_llm=lambda tid, turno: _coro(_llm(provider)),
        capacidades=lambda tid: _coro(frozenset({"pack_agenda"})),
        memoria=MemoriaWa(url="x", client=_FakeRedis()),
        sender=sender,
    )
    await agente.atender(MensajeWa(message_id="w", telefono=TEL, phone_number_id=PNID, texto="hola"),
                         _tenant_resuelto())
    enviado = sender.envios[0][2]                       # el texto realmente enviado por Kapso
    assert "*Hola*" in enviado and "**" not in enviado  # negrita Markdown → WhatsApp
    assert "###" not in enviado and "Cita" in enviado


async def test_e2e_usa_orquestador_y_persona_del_negocio(tenant):
    await _seed(tenant.engine)
    provider = _ScriptedProvider([LLMResponse(text="Hola, ¿en qué te ayudo con tu cita?", tool_calls=[])])
    turnos_pedidos = []

    async def resolver_llm(tid, turno):
        turnos_pedidos.append(turno)
        return _llm(provider)

    agente = AgenteWa(
        abrir_tenant=_abrir_factory(tenant.engine),
        resolver_llm=resolver_llm,
        capacidades=lambda tid: _coro(frozenset({"pack_agenda"})),
        memoria=MemoriaWa(url="x", client=_FakeRedis()),
        sender=_FakeSender(),
    )
    await agente.atender(MensajeWa(message_id="w", telefono=TEL, phone_number_id=PNID, texto="hola"),
                         _tenant_resuelto())
    assert turnos_pedidos == [Turno.ORQUESTADOR]            # modelo capaz para lo complejo
    assert "Cordial y breve." in provider.generaciones[0]["system"]  # persona del negocio en el system


# --- fallback ---------------------------------------------------------------
async def test_fallback_elegante_si_el_llm_falla(tenant):
    sender = _FakeSender()

    async def resolver_llm_boom(tid, turno):
        raise RuntimeError("LLM caído")

    agente = AgenteWa(
        abrir_tenant=_abrir_factory(tenant.engine),
        resolver_llm=resolver_llm_boom,
        capacidades=lambda tid: _coro(frozenset({"pack_agenda"})),
        memoria=MemoriaWa(url="x", client=_FakeRedis()),
        sender=sender,
    )
    from apps.wa.agent import FALLBACK
    texto = await agente.atender(MensajeWa(message_id="w", telefono=TEL, phone_number_id=PNID, texto="hola"),
                                 _tenant_resuelto())
    assert texto == FALLBACK
    assert sender.envios == [(PNID, TEL, FALLBACK)]        # mensaje amable, sin exponer el error


async def test_envio_fallido_no_tumba_el_job(tenant):
    await _seed(tenant.engine)

    class _SenderBoom:
        async def enviar_texto(self, **kw):
            raise RuntimeError("Kapso caído")

    provider = _ScriptedProvider([LLMResponse(text="Hola, ¿cuándo te agendo?", tool_calls=[])])
    agente = AgenteWa(
        abrir_tenant=_abrir_factory(tenant.engine),
        resolver_llm=lambda tid, turno: _coro(_llm(provider)),
        capacidades=lambda tid: _coro(frozenset({"pack_agenda"})),
        memoria=MemoriaWa(url="x", client=_FakeRedis()),
        sender=_SenderBoom(),
    )
    # El envío revienta pero atender() no propaga (el job no se cae).
    texto = await agente.atender(
        MensajeWa(message_id="w", telefono=TEL, phone_number_id=PNID, texto="hola"), _tenant_resuelto()
    )
    assert texto == "Hola, ¿cuándo te agendo?"


def _coro(valor):
    """Envuelve un valor en una corrutina (para lambdas async-like en los fakes)."""
    async def _c(): return valor
    return _c()
