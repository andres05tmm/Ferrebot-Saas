"""Herramientas del Bot PIM (`ai/obra_tools.py`) — Fase 6. Servicios y proveedor de visión MOCKEADOS.

Cubre: las 3 tools felices (horas / reporte diario / gasto por recibo); el gasto de baja confianza a
la bandeja de revisión; el GUARDARRAÍL (la identidad viaja por el Contexto/ContextoTelegram, NUNCA por
args del modelo); el gateo por flag + rol; la idempotencia de horas (reintento → replay, sin duplicar);
y el ruteo del despachador hacia el pack (incl. fail-closed sin deps). Sin BD, sin red, sin llaves: el
proveedor de visión es un doble que devuelve el JSON del recibo, así se ejercita `extraer_recibo` real
con el modelo inyectado por el factory (cierra el cleanup del default placeholder).
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto, ErrorTool, Resultado
from ai.obra_tools import (
    ContextoTelegram,
    ObraDeps,
    catalogo_visible,
    ejecutar,
    exponer_catalogo,
)
from core.config.timezone import today_co
from core.llm.base import ImageBlock, LLMResponse, ToolCall
from core.llm.factory import LLMResuelto, PlataformaLLM
from modules.caja.errors import CajaNoAbierta
from modules.maquinaria.errors import MaquinaInexistente
from modules.maquinaria.service import ResultadoRegistroHoras
from modules.obra.errors import ObraInexistente

# --- JSON que devuelve el (falso) modelo de visión para el recibo Bancolombia ---
_RECIBO_OK = (
    '{"fecha":"2026-07-05","valor":1150000,"referencia":"M12345",'
    '"tipo_transaccion":"transferencia","entidad_o_producto_origen":"Ahorros",'
    '"destino":"Ferreteria El Tornillo","descripcion":"Compra cemento","confianza":0.95}'
)
_RECIBO_DUDOSO = '{"valor":80000,"confianza":0.4,"referencia":null}'      # confianza < 0.7
_RECIBO_SIN_MONTO = '{"valor":null,"confianza":0.9}'                      # no se pudo leer el monto


# --------------------------- Dobles de servicio ---------------------------
def _maquina(id=1, nombre="Retroexcavadora CAT"):
    return SimpleNamespace(id=id, nombre=nombre)


def _obra(id=10, nombre="Torre Norte"):
    return SimpleNamespace(id=id, nombre=nombre)


def _res_horas(*, replay=False, horas="6", facturables="6", minimo_cubierto=True, ingreso="900000"):
    return ResultadoRegistroHoras(
        registro_id=1, maquina_id=1, obra_id=10, fecha=date(2026, 7, 7),
        horas_trabajadas=Decimal(horas), horas_facturables=Decimal(facturables),
        minimo_cubierto=minimo_cubierto, precio_hora=Decimal("150000"),
        ingreso=Decimal(ingreso), origen_registro="TELEGRAM_BOT", replay=replay,
    )


class _FakeMaquinaria:
    def __init__(self, *, por_id=None, por_q=None, resultado=None):
        self._por_id = por_id or {}
        self._por_q = por_q if por_q is not None else [_maquina()]
        self._resultado = resultado if resultado is not None else _res_horas()
        self.registrado = None

    async def obtener(self, maquina_id):
        m = self._por_id.get(maquina_id)
        if m is None:
            raise MaquinaInexistente(maquina_id)
        return m

    async def listar(self, *, estado=None, q=None):
        return list(self._por_q)

    async def registrar_horas(self, maquina_id, datos):
        self.registrado = SimpleNamespace(maquina_id=maquina_id, datos=datos)
        return self._resultado


class _FakeObras:
    def __init__(self, *, obras=None, reporte=None, error=None):
        self._obras = obras if obras is not None else [_obra()]
        self._reporte = reporte
        self._error = error
        self.creado = None

    async def obtener(self, obra_id):
        for o in self._obras:
            if o.id == obra_id:
                return o
        raise ObraInexistente(obra_id)

    async def listar(self, *, cliente_id=None, estado=None):
        return list(self._obras)

    async def crear_reporte(self, obra_id, datos):
        if self._error is not None:
            raise self._error
        self.creado = SimpleNamespace(obra_id=obra_id, datos=datos)
        return self._reporte


class _FakeCaja:
    def __init__(self, *, gasto_id=77, replay=False, error=None):
        self._gasto_id = gasto_id
        self._replay = replay
        self._error = error
        self.kwargs = None

    async def registrar_gasto(self, **kwargs):
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        return SimpleNamespace(gasto=SimpleNamespace(id=self._gasto_id), replay=self._replay)


class _FakeVisionProvider:
    """Doble del proveedor de visión: devuelve el JSON del recibo. `extraer_recibo` (real) lo parsea."""

    nombre = "fake"
    api_key = "sk-fake"

    def __init__(self, payload: str):
        self._payload = payload
        self.model_usado = None

    async def generate(self, *, messages, tools, model, system=None, **kwargs):
        self.model_usado = model            # para verificar la inyección del modelo del factory
        return LLMResponse(text=self._payload)


def _resolver_vision(provider, model="fake-vision-model"):
    async def _r(tenant_id):
        return LLMResuelto(provider=provider, model=model, provider_nombre="fake")
    return _r


async def _no_vision(tenant_id):   # las tools que no son de recibo NO deben resolver visión
    raise AssertionError("resolver_vision no debería invocarse en esta tool")


# --------------------------- Contexto / canal / calls ---------------------
def _ctx(*, rol="vendedor", caps=frozenset({"maquinaria", "obras"}), usuario_id=42, key="idem-1"):
    return Contexto(
        tenant_id=1, usuario_id=usuario_id, rol=rol, origen="bot",
        idempotency_key=key, capacidades=caps,
    )


def _canal(*, con_imagen=True, uid="555", mid="msg-9", url="https://bucket/x.jpg"):
    imagen = ImageBlock.desde_base64("aGVsbG8=", "image/jpeg") if con_imagen else None
    return ContextoTelegram(
        imagen=imagen, telegram_user_id=uid, telegram_message_id=mid, comprobante_url=url,
    )


def _call(nombre, **arguments):
    return ToolCall(id="t", name=nombre, arguments=arguments)


# =========================================================================
# 1) registrar_horas_maquina — feliz
# =========================================================================
async def test_registrar_horas_maquina_feliz():
    maq = _FakeMaquinaria(por_q=[_maquina(id=1, nombre="Retroexcavadora CAT")], resultado=_res_horas())
    deps = ObraDeps(
        maquinaria=maq, obras=_FakeObras(obras=[_obra(id=10, nombre="Torre Norte")]),
        caja=_FakeCaja(), resolver_vision=_no_vision,
    )
    r = await ejecutar(_call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6), _ctx(), deps)

    assert isinstance(r, Resultado)
    assert r.evento == "horas_registradas" and r.idempotente == "aplicada"
    assert "facturables" in r.resumen and "$900.000" in r.resumen and "cubierto" in r.resumen
    assert r.data["ingreso"] == "900000" and r.data["minimo_cubierto"] is True
    # Lo que se mandó al servicio: obra resuelta, HOY, origen del canal, operador = usuario del Contexto.
    d = maq.registrado.datos
    assert maq.registrado.maquina_id == 1
    assert d.obra_id == 10 and d.fecha == today_co()
    assert d.origen_registro == "TELEGRAM_BOT" and d.operador_id == 42
    assert d.horas_trabajadas == Decimal("6")


# =========================================================================
# 2) reporte_diario_obra — feliz + identidad del canal
# =========================================================================
async def test_reporte_diario_obra_feliz():
    reporte = SimpleNamespace(
        id=5, obra_id=10, fecha=date(2026, 7, 7),
        m2_ejecutados=Decimal("12.5"), m3_ejecutados=None,
    )
    obras = _FakeObras(obras=[_obra(id=10, nombre="Torre Norte")], reporte=reporte)
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=obras, caja=_FakeCaja(),
        resolver_vision=_no_vision, canal=_canal(uid="555"),
    )
    r = await ejecutar(
        _call("reporte_diario_obra", obra="Torre", avance="Fundida de placa nivel 3", m2=12.5),
        _ctx(), deps,
    )
    assert isinstance(r, Resultado) and r.evento == "reporte_diario_creado"
    assert r.data["reporte_id"] == 5 and "12.5 m²" in r.resumen
    # La identidad de Telegram sale del canal (no del modelo) y el origen es TELEGRAM_BOT.
    d = obras.creado.datos
    assert obras.creado.obra_id == 10
    assert d.telegram_user_id == "555" and d.origen_registro == "TELEGRAM_BOT"
    assert d.avance_descripcion == "Fundida de placa nivel 3"


# =========================================================================
# 3) registrar_gasto_recibo — feliz (alta confianza) + inyección del modelo
# =========================================================================
async def test_registrar_gasto_recibo_feliz():
    prov = _FakeVisionProvider(_RECIBO_OK)
    caja = _FakeCaja(gasto_id=77)
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(obras=[_obra(id=10, nombre="Torre Norte")]),
        caja=caja, resolver_vision=_resolver_vision(prov), canal=_canal(uid="555", mid="msg-9"),
    )
    # El modelo manda la categoría en texto libre ("combustible"); el pack la NORMALIZA al enum del
    # vertical ("COMBUSTIBLE"). La POS es otra taxonomía (enum fijo): cae a "otros" (valor válido).
    r = await ejecutar(
        _call("registrar_gasto_recibo", categoria_gasto="combustible", obra="Torre"), _ctx(), deps,
    )
    assert isinstance(r, Resultado) and r.evento == "gasto_registrado"
    assert r.idempotente == "aplicada" and r.data["requiere_revision"] is False
    assert r.data["monto"] == "1150000" and "$1.150.000" in r.resumen
    # El modelo de visión lo resolvió el factory (no el default placeholder de ai/vision).
    assert prov.model_usado == "fake-vision-model"
    # Persistencia: origen + identidad del canal + idempotencia por message_id + imputación a la obra.
    k = caja.kwargs
    assert k["origen_registro"] == "TELEGRAM_BOT"
    assert k["telegram_user_id"] == "555" and k["telegram_message_id"] == "msg-9"
    assert k["idempotency_key"] == "telegram:gasto:msg-9"
    assert k["monto"] == Decimal("1150000") and k["requiere_revision"] is False
    assert k["obra_id"] == 10 and k["categoria_gasto"] == "COMBUSTIBLE"   # normalizado al enum vertical
    assert k["categoria"] == "otros" and k["numero_referencia"] == "M12345"  # POS: enum fijo válido
    assert k["comprobante_url"] == "https://bucket/x.jpg"
    assert k["usuario_id"] == 42


# =========================================================================
# 4) registrar_gasto_recibo — baja confianza → bandeja de revisión
# =========================================================================
async def test_registrar_gasto_recibo_baja_confianza_va_a_revision():
    prov = _FakeVisionProvider(_RECIBO_DUDOSO)          # confianza 0.4 < 0.7
    caja = _FakeCaja()
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=caja,
        resolver_vision=_resolver_vision(prov), canal=_canal(),
    )
    r = await ejecutar(_call("registrar_gasto_recibo"), _ctx(), deps)

    assert isinstance(r, Resultado) and r.data["requiere_revision"] is True
    assert "revisión" in r.resumen.lower() or "REVISIÓN" in r.resumen
    assert caja.kwargs["requiere_revision"] is True
    # Sin categoría explícita: la POS cae al valor fijo válido "otros" y la del vertical queda None
    # (la fija el humano en la bandeja de revisión). Nunca un texto libre que el enum de la BD rechace.
    assert caja.kwargs["categoria"] == "otros" and caja.kwargs["categoria_gasto"] is None


# =========================================================================
# 5) GUARDARRAÍL — la identidad NO puede llegar por args del modelo
# =========================================================================
async def test_guardarrail_identidad_no_por_args():
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=_FakeCaja(), resolver_vision=_no_vision,
    )
    # El modelo intenta colar telegram_user_id en el reporte → rechazado como validacion (extra=forbid).
    e1 = await ejecutar(
        _call("reporte_diario_obra", obra="Torre", avance="x", telegram_user_id="999"), _ctx(), deps,
    )
    assert isinstance(e1, ErrorTool) and e1.error == "validacion" and e1.recuperable
    # Y el tenant_id en horas → igual: la identidad SIEMPRE del Contexto, nunca del modelo.
    e2 = await ejecutar(
        _call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6, tenant_id=999),
        _ctx(), deps,
    )
    assert isinstance(e2, ErrorTool) and e2.error == "validacion"


# =========================================================================
# 6) Gateo por flag (capacidad) y por rol
# =========================================================================
def test_catalogo_gateado_por_flag_y_rol():
    # Sin flags de construcción: nada visible.
    assert catalogo_visible(_ctx(caps=frozenset())) == []
    # Con maquinaria + obras y rol vendedor: las 3.
    nombres = {spec.name for spec in exponer_catalogo(_ctx())}
    assert nombres == {"registrar_horas_maquina", "reporte_diario_obra", "registrar_gasto_recibo"}
    # Solo `maquinaria`: únicamente la de horas.
    solo_maq = {spec.name for spec in exponer_catalogo(_ctx(caps=frozenset({"maquinaria"})))}
    assert solo_maq == {"registrar_horas_maquina"}


async def test_ejecutar_gatea_capacidad_y_rol():
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=_FakeCaja(), resolver_vision=_no_vision,
    )
    # Sin la capacidad `maquinaria`: capacidad_no_habilitada (defensa en profundidad del pack).
    sin_cap = await ejecutar(
        _call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6),
        _ctx(caps=frozenset({"obras"})), deps,
    )
    assert isinstance(sin_cap, ErrorTool) and sin_cap.error == "capacidad_no_habilitada"
    # Rol por debajo del mínimo (un "cliente" no alcanza `vendedor`): permiso_denegado.
    sin_rol = await ejecutar(
        _call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6),
        _ctx(rol="cliente"), deps,
    )
    assert isinstance(sin_rol, ErrorTool) and sin_rol.error == "permiso_denegado"


# =========================================================================
# 7) IDEMPOTENCIA de horas — reintento → replay, sin duplicar (invariante carve-out)
# =========================================================================
async def test_horas_reintento_surface_replay():
    # El servicio detecta el parte del día por clave natural y devuelve replay=True: el pack lo refleja
    # (duplicada) y NO hay un segundo registro → el cargo a cartera de Fase 5 se asienta una sola vez.
    maq = _FakeMaquinaria(resultado=_res_horas(replay=True))
    deps = ObraDeps(maquinaria=maq, obras=_FakeObras(), caja=_FakeCaja(), resolver_vision=_no_vision)
    r = await ejecutar(_call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6), _ctx(), deps)
    assert isinstance(r, Resultado) and r.idempotente == "duplicada"
    assert "ya estaba registrado" in r.resumen


# =========================================================================
# 8) Resolución difusa: ambigua → pregunta; sin coincidencia → error recuperable
# =========================================================================
async def test_maquina_ambigua_pregunta_por_candidatos():
    maq = _FakeMaquinaria(por_q=[_maquina(id=1, nombre="Retro A"), _maquina(id=2, nombre="Retro B")])
    deps = ObraDeps(maquinaria=maq, obras=_FakeObras(), caja=_FakeCaja(), resolver_vision=_no_vision)
    r = await ejecutar(_call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6), _ctx(), deps)
    assert isinstance(r, Resultado) and len(r.data["candidatos"]) == 2
    assert maq.registrado is None                       # no se registró nada ante la ambigüedad


async def test_obra_no_encontrada_es_error_recuperable():
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(obras=[_obra(id=10, nombre="Torre Norte")]),
        caja=_FakeCaja(), resolver_vision=_no_vision,
    )
    r = await ejecutar(
        _call("registrar_horas_maquina", maquina="Retro", obra="Bodega Sur", horas=6), _ctx(), deps,
    )
    assert isinstance(r, ErrorTool) and r.error == "obra_no_encontrada" and r.recuperable


# =========================================================================
# 9) gasto_recibo — sin imagen y recibo ilegible: no crea gasto fantasma
# =========================================================================
async def test_gasto_recibo_sin_imagen_pide_foto():
    caja = _FakeCaja()
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=caja,
        resolver_vision=_resolver_vision(_FakeVisionProvider(_RECIBO_OK)), canal=_canal(con_imagen=False),
    )
    r = await ejecutar(_call("registrar_gasto_recibo"), _ctx(), deps)
    assert isinstance(r, ErrorTool) and r.error == "sin_imagen" and r.recuperable
    assert caja.kwargs is None                          # no se tocó la caja


async def test_gasto_recibo_sin_monto_no_registra():
    caja = _FakeCaja()
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=caja,
        resolver_vision=_resolver_vision(_FakeVisionProvider(_RECIBO_SIN_MONTO)), canal=_canal(),
    )
    r = await ejecutar(_call("registrar_gasto_recibo"), _ctx(), deps)
    assert isinstance(r, ErrorTool) and r.error == "recibo_ilegible" and r.recuperable
    assert caja.kwargs is None


async def test_gasto_recibo_caja_cerrada():
    caja = _FakeCaja(error=CajaNoAbierta(42))
    deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(), caja=caja,
        resolver_vision=_resolver_vision(_FakeVisionProvider(_RECIBO_OK)), canal=_canal(),
    )
    r = await ejecutar(_call("registrar_gasto_recibo"), _ctx(), deps)
    assert isinstance(r, ErrorTool) and r.error == "caja_cerrada" and r.recuperable


# =========================================================================
# 10) Ruteo del DESPACHADOR hacia el pack (mis ediciones en ai/dispatcher.py)
# =========================================================================
def _disp():
    class _FakeConfigStore:
        async def overrides(self, empresa_id):
            return {}

    class _FakeKeyStore:
        async def api_key(self, empresa_id, provider):
            return None

    return Dispatcher(
        config_store=_FakeConfigStore(), key_store=_FakeKeyStore(),
        plataforma=PlataformaLLM(
            provider="openai", model_worker="gpt-4o-mini",
            model_orquestador="gpt-4o", keys={"openai": "sk-test"},
        ),
    )


def test_dispatcher_expone_obra_solo_con_flags():
    obra = {"registrar_horas_maquina", "reporte_diario_obra", "registrar_gasto_recibo"}
    con = {t.name for t in _disp().exponer_catalogo(_ctx(caps=frozenset({"maquinaria", "obras"})))}
    assert obra <= con
    retail = {t.name for t in _disp().exponer_catalogo(_ctx(caps=frozenset({"ventas", "caja"})))}
    assert obra.isdisjoint(retail)


async def test_dispatcher_rutea_al_pack_y_falla_cerrado_sin_deps():
    disp = _disp()
    tc = _call("registrar_horas_maquina", maquina="Retro", obra="Torre", horas=6)
    # Sin `recursos.obra` cableado: fail-closed (capacidad_no_habilitada), no revienta.
    rec_vacio = Recursos(deps=None, catalogo=None, umbrales=None)
    e = await disp.ejecutar(tc, _ctx(), rec_vacio)
    assert isinstance(e, ErrorTool) and e.error == "capacidad_no_habilitada"
    # Con deps del pack: el despachador delega al pack (no pasa por rieles de venta) y registra.
    obra_deps = ObraDeps(
        maquinaria=_FakeMaquinaria(), obras=_FakeObras(obras=[_obra(id=10, nombre="Torre Norte")]),
        caja=_FakeCaja(), resolver_vision=_no_vision,
    )
    r = await disp.ejecutar(tc, _ctx(), Recursos(deps=None, catalogo=None, umbrales=None, obra=obra_deps))
    assert isinstance(r, Resultado) and r.evento == "horas_registradas"
