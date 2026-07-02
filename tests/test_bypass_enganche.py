"""Enganche del Bypass al flujo del bot (Paso A: ventas).

El Bypass (camino rápido sin IA) ya se conecta al handler ANTES del modelo. Contrato fijado con fakes:

  - match: el bypass devuelve una Respuesta → el handler responde con ESO y NO llama al modelo;
  - no-match: el bypass devuelve None (CaeAlModelo) → el turno cae al modelo, como antes;
  - resiliencia: si el bypass revienta, el turno NO crashea: cae al modelo;
  - cableado: `crear_bypass_factory` arma un Bypass no-None por sesión;
  - adaptador `CatalogoBypassExacto`: match único → producto (con su esquema); 0/>1 → None.
"""
from decimal import Decimal

from ai.agent import RespuestaAgente
from ai.bypass import ProductoBypass
from ai.envelope import Contexto, Resultado
from ai.turno import crear_turno_handler
from apps.bot.catalogo import CatalogoBypassExacto
from apps.bot.ports import UpdateBot
from apps.bot.wiring import crear_bypass_factory
from core.llm.factory import LLMResuelto
from modules.ventas.service import ProductoPrecio


# --------------------------------- fakes ----------------------------------
class _FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):
        raise AssertionError("no debería generar (el bucle va falseado)")


class _SpyDispatcher:
    """Cuenta las selecciones de proveedor: 0 = el modelo NO se invocó."""

    def __init__(self) -> None:
        self.selecciones = 0

    async def seleccionar_proveedor(self, empresa_id, *, turno=None) -> LLMResuelto:
        self.selecciones += 1
        return LLMResuelto(provider=_FakeLLM(), model="m", provider_nombre="fake")


class _FakeMemoria:
    def __init__(self) -> None:
        self.guardados: list[tuple[int, str, str]] = []

    async def cargar_historial(self, chat_id, *, limite=8):
        return []

    async def leer_entidades(self, chat_id):
        return {}

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        self.guardados.append((chat_id, usuario, asistente))


class _FakeCostos:
    async def acumular(self, **kw):
        pass


class _FakeNotificador:
    def __init__(self) -> None:
        self.enviados: list[tuple[int, str]] = []

    async def responder(self, chat_id: int, texto: str) -> None:
        self.enviados.append((chat_id, texto))


class _FakeBypass:
    """`intentar` devuelve la Respuesta pre-cargada (match) o None (CaeAlModelo)."""

    def __init__(self, respuesta=None) -> None:
        self._r = respuesta
        self.llamado_con = None

    async def intentar(self, texto, ctx, recursos):
        self.llamado_con = (texto, ctx, recursos)
        return self._r


class _FakeEjecutar:
    def __init__(self, respuesta: RespuestaAgente) -> None:
        self._r = respuesta
        self.llamadas = 0

    async def __call__(self, **kw) -> RespuestaAgente:
        self.llamadas += 1
        return self._r


class _BoomBypass:
    """Simula un fallo del bypass: `intentar` revienta (debe caer al modelo, no tumbar el turno)."""

    async def intentar(self, texto, ctx, recursos):
        raise RuntimeError("boom en el bypass")


class _FakeInventario:
    """Capa exacta falsa: devuelve hasta `limite` de las coincidencias pre-cargadas (id, nombre)."""

    def __init__(self, coincidencias):
        self._c = list(coincidencias)

    async def buscar_exacta(self, query, limite):
        return self._c[:limite]


class _FakeVentasObtener:
    """Read de producto falso: id → ProductoPrecio (que arma el EsquemaPrecio)."""

    def __init__(self, productos):
        self._p = {p.id: p for p in productos}

    async def obtener_producto(self, producto_id):
        return self._p.get(producto_id)


def _pp(id_, nombre, precio="1000") -> ProductoPrecio:
    return ProductoPrecio(id=id_, nombre=nombre, precio_venta=Decimal(precio), iva=0, activo=True)


# --------------------------------- helpers --------------------------------
_SESSION = object()


def _ctx(capacidades: frozenset[str] = frozenset({"bot_telegram", "ventas"})) -> Contexto:
    # El bypass registra ventas → requiere la feature fina `ventas` (ADR 0021); el default la trae.
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    capacidades=capacidades)


def _update(texto="3 vinilo", chat_id=555) -> UpdateBot:
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=texto)


def _handler(*, bypass, dispatcher, ejecutar, memoria):
    return crear_turno_handler(
        dispatcher=dispatcher,
        memoria=lambda s: memoria,
        costos=lambda s: _FakeCostos(),
        crear_recursos=lambda s: object(),
        ejecutar=ejecutar,
        crear_bypass=lambda s: bypass,
    )


# ------------------------------- tests ------------------------------------
async def test_bypass_match_responde_y_no_llama_al_modelo():
    # El bypass resuelve la venta → el handler debe responder con ESE resultado y NO tocar el modelo.
    bypass = _FakeBypass(Resultado(data={}, resumen="Venta #1 registrada."))
    disp = _SpyDispatcher()
    ejecutar = _FakeEjecutar(RespuestaAgente(texto="(modelo)", ruta="texto"))
    notif = _FakeNotificador()
    handler = _handler(bypass=bypass, dispatcher=disp, ejecutar=ejecutar, memoria=_FakeMemoria())

    await handler(_update("3 vinilo"), _ctx(), _SESSION, notif)

    assert notif.enviados == [(555, "Venta #1 registrada.")]   # respondió con el resultado del bypass
    assert disp.selecciones == 0 and ejecutar.llamadas == 0     # el modelo NO se invocó


async def test_bypass_no_match_cae_al_modelo():
    # El bypass no aplica (None = CaeAlModelo) → el turno sigue al modelo, como hoy.
    bypass = _FakeBypass(None)
    disp = _SpyDispatcher()
    ejecutar = _FakeEjecutar(RespuestaAgente(texto="respuesta del modelo", ruta="texto"))
    notif = _FakeNotificador()
    handler = _handler(bypass=bypass, dispatcher=disp, ejecutar=ejecutar, memoria=_FakeMemoria())

    await handler(_update("hola, ¿qué tal?"), _ctx(), _SESSION, notif)

    assert disp.selecciones == 1 and ejecutar.llamadas == 1     # cayó al modelo
    assert notif.enviados == [(555, "respuesta del modelo")]


async def test_bypass_excepcion_no_tumba_el_turno_cae_al_modelo():
    # Resiliencia: si el bypass revienta, el turno NO crashea: cae al modelo.
    disp = _SpyDispatcher()
    ejecutar = _FakeEjecutar(RespuestaAgente(texto="respuesta del modelo", ruta="texto"))
    notif = _FakeNotificador()
    handler = _handler(bypass=_BoomBypass(), dispatcher=disp, ejecutar=ejecutar, memoria=_FakeMemoria())

    await handler(_update("3 vinilo"), _ctx(), _SESSION, notif)   # NO debe propagar

    assert disp.selecciones == 1 and ejecutar.llamadas == 1       # cayó al modelo
    assert notif.enviados == [(555, "respuesta del modelo")]


async def test_bypass_sin_capacidad_ventas_queda_inerte_y_cae_al_modelo():
    # Invariante (ADR 0021): sin la feature `ventas`, el bypass NO puede registrar ventas — ni se
    # intenta. El turno cae al modelo (que tampoco tendrá la tool registrar_venta en su catálogo).
    bypass = _FakeBypass(Resultado(data={}, resumen="Venta #1 registrada."))
    disp = _SpyDispatcher()
    ejecutar = _FakeEjecutar(RespuestaAgente(texto="respuesta del modelo", ruta="texto"))
    notif = _FakeNotificador()
    handler = _handler(bypass=bypass, dispatcher=disp, ejecutar=ejecutar, memoria=_FakeMemoria())

    ctx = _ctx(capacidades=frozenset({"bot_telegram", "pack_agenda"}))   # sin ventas ni pos
    await handler(_update("3 vinilo"), ctx, _SESSION, notif)

    assert bypass.llamado_con is None                            # el bypass NI se intentó
    assert disp.selecciones == 1 and ejecutar.llamadas == 1      # cayó al modelo
    assert notif.enviados == [(555, "respuesta del modelo")]


async def test_bypass_con_metapack_pos_ejecuta():
    # Compat: `pos` (meta-pack) satisface `ventas` por expansión — Punto Rojo sigue igual.
    bypass = _FakeBypass(Resultado(data={}, resumen="Venta #1 registrada."))
    disp = _SpyDispatcher()
    ejecutar = _FakeEjecutar(RespuestaAgente(texto="(modelo)", ruta="texto"))
    notif = _FakeNotificador()
    handler = _handler(bypass=bypass, dispatcher=disp, ejecutar=ejecutar, memoria=_FakeMemoria())

    await handler(_update("3 vinilo"), _ctx(capacidades=frozenset({"bot_telegram", "pos"})), _SESSION, notif)

    assert notif.enviados == [(555, "Venta #1 registrada.")]
    assert disp.selecciones == 0 and ejecutar.llamadas == 0


def test_wiring_arma_bypass_no_none():
    # El default real del seam: una factory por sesión que arma un Bypass (con .intentar) no-None.
    factory = crear_bypass_factory(object())   # dispatcher centinela (no se usa al construir)
    bypass = factory(object())                  # session centinela (no abre sesión)
    assert bypass is not None and hasattr(bypass, "intentar")


# --------------------- adaptador CatalogoBypassExacto ---------------------
async def test_producto_exacto_match_unico_devuelve_producto():
    inv = _FakeInventario([(7, "vinilo")])
    ventas = _FakeVentasObtener([_pp(7, "vinilo", "20000")])
    prod = await CatalogoBypassExacto(inv, ventas).producto_exacto("vinilo")
    assert isinstance(prod, ProductoBypass)
    assert prod.id == 7 and prod.nombre == "vinilo"
    assert prod.esquema.precio_venta == Decimal("20000")          # cargó el EsquemaPrecio


async def test_producto_exacto_ambiguo_devuelve_none():
    inv = _FakeInventario([(7, "vinilo mate"), (8, "vinilo brillante")])   # >1 → no adivina
    prod = await CatalogoBypassExacto(inv, _FakeVentasObtener([])).producto_exacto("vinilo")
    assert prod is None


async def test_producto_exacto_inexistente_devuelve_none():
    inv = _FakeInventario([])                                      # 0 coincidencias
    prod = await CatalogoBypassExacto(inv, _FakeVentasObtener([])).producto_exacto("taladro")
    assert prod is None
