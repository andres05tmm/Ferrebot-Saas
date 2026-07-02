"""Entregable 5.3 — convergencia del bypass por el despachador.

Pin de lo que el checkpoint exige:
  1. Corte match/ejecución: el match emite un ToolCall normalizado a dispatcher.ejecutar; el bypass
     NO llama a VentaService directamente.
  2. No-match → None (CaeAlModelo): el bypass no ejecuta nada; el turno cae al modelo.
  3. Paridad directo-vs-despachador: mismas frases → mismo efecto en VentaService (enteros, fracción
     mixta, venta por peso, venta por caja). La normalización a ToolCall(items:[{producto_id,
     cantidad}]) preserva la cantidad Decimal y el total que calcula FerreBot.
  4. R1/R2 inertes para la venta por bypass (producto resuelto → candidatos=1 → Ejecutar; sin
     precio_unitario → R2 no corre) e idempotencia (mismo key → no duplica). R3 + idempotencia del
     sink de ejecución (dispatcher.ejecutar) sobre una mutación confirmable (gasto): contrato que la
     convergencia hereda.
  5. Doble lectura (decisión #5b): el camino convergente NO agrega round-trips a Postgres — R1 lee el
     producto de `recursos.resueltos` (que el bypass pre-cargó), no vía `catalogo.obtener`.

NOTA de alcance: el parser del bypass hoy solo produce VENTA (gasto/fiado/abono son deshabilitadores
→ caen al modelo). Por eso R3 se verifica sobre el sink real (dispatcher.ejecutar), el mismo punto al
que el bypass converge; cuando se porten esos intents, heredan R3 + idempotencia sin código nuevo.
"""
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pytest

from ai.bypass import Bypass, ProductoBypass, normalizar_slug
from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto, Resultado
from ai.ports import ProductoCatalogo, Umbrales
from ai.rieles import Confirmar
from ai.tools import Deps
from core.config.timezone import now_co
from core.llm.base import ToolCall
from modules.caja.service import CajaService
from modules.inventario.precios import FraccionPrecio
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear, VentaLeer
from modules.ventas.service import ProductoPrecio, VentaService

SECRET = "x"


# --------------------------------- productos ------------------------------

def _frac(decimal, total):
    return FraccionPrecio(decimal=Decimal(decimal), precio_total=Decimal(total))


VINILO = ProductoPrecio(id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True,
                        fracciones=(_frac("0.5", "12000"),))
MANGUERA = ProductoPrecio(id=8, nombre="manguera", precio_venta=Decimal("8000"), iva=0, activo=True,
                          fracciones=(_frac("0.5", "5000"),))
PUNTILLAS = ProductoPrecio(id=9, nombre="puntillas", precio_venta=Decimal("3000"), iva=0, activo=True,
                           fracciones=(_frac("0.5", "1600"),))      # por libra: media libra
CAJA = ProductoPrecio(id=10, nombre="tornillos caja", precio_venta=Decimal("25000"), iva=0, activo=True)
ESCALONADO = ProductoPrecio(id=11, nombre="cemento", precio_venta=Decimal("20000"), iva=0, activo=True,
                            precio_umbral=Decimal("10"), precio_bajo_umbral=Decimal("20000"),
                            precio_sobre_umbral=Decimal("18000"))
LIJA = ProductoPrecio(id=12, nombre="lija", precio_venta=Decimal("2000"), iva=0, activo=True)  # sin fracciones
# Granel: puntilla por gramo (caja 500 g, precio_venta = precio de la caja) y lija esmeril por cm
# (precio_venta por 100 cm). El motor cobra exacto vía unidad_medida (no cantidad×precio_venta).
PUNTILLA_GRM = ProductoPrecio(id=15, nombre="puntilla 1 sin cabeza", precio_venta=Decimal("7000"),
                              iva=0, activo=True, unidad_medida="GRM")
LIJA_ESMERIL = ProductoPrecio(id=16, nombre="lija esmeril n36", precio_venta=Decimal("22000"),
                              iva=0, activo=True, unidad_medida="Cms")
# Producto cuyo nombre lleva "para <Palabra capitalizada>": NO es un cliente ("para Juan").
BROCA_MURO = ProductoPrecio(id=17, nombre="Broca para Muro 1/4", precio_venta=Decimal("5000"),
                            iva=0, activo=True)


def _pb(p: ProductoPrecio) -> ProductoBypass:
    return ProductoBypass(id=p.id, nombre=p.nombre, esquema=p.esquema())


def _pc(p: ProductoPrecio) -> ProductoCatalogo:
    return ProductoCatalogo(id=p.id, nombre=p.nombre, activo=p.activo, esquema=p.esquema())


# --------------------------------- fakes ----------------------------------

class _VentasRepo:
    def __init__(self, productos, stock=Decimal("1000")):
        self._productos = {p.id: p for p in productos}
        self._stock = stock
        self._cons = 0
        self._por_key: dict[str, VentaLeer] = {}
        self.ultimo_header = None
        self.creadas = 0

    async def buscar_por_idempotency(self, key):
        return self._por_key.get(key)

    async def obtener_producto(self, pid):
        return self._productos.get(pid)

    async def lock_inventario(self, pid):
        return self._stock

    async def siguiente_consecutivo(self):
        self._cons += 1
        return self._cons

    async def crear_venta(self, header):
        self.ultimo_header = header
        self.creadas += 1
        venta = VentaLeer(
            id=self.creadas, consecutivo=header.consecutivo, cliente_id=header.cliente_id,
            vendedor_id=header.vendedor_id, fecha=now_co(), subtotal=header.subtotal,
            impuestos=header.impuestos, total=header.total, metodo_pago=header.metodo_pago,
            estado="completada", origen=header.origen, idempotency_key=header.idempotency_key,
        )
        if header.idempotency_key:
            self._por_key[header.idempotency_key] = venta
        return venta


class _CatalogoBypass:
    """producto_exacto por slug; cuenta lecturas (la resolución del bypass)."""

    def __init__(self, productos):
        self._por_slug = {normalizar_slug(p.nombre): _pb(p) for p in productos}
        self.lecturas = 0

    async def producto_exacto(self, slug):
        self.lecturas += 1
        return self._por_slug.get(slug)


class _CatalogoPrecios:
    """obtener(id) del despachador; cuenta llamadas (debe ser 0 en el camino convergente)."""

    def __init__(self, productos):
        self._por_id = {p.id: _pc(p) for p in productos}
        self.obtener_llamadas = 0

    async def obtener(self, pid):
        self.obtener_llamadas += 1
        return self._por_id.get(pid)


class _Umbrales:
    def __init__(self, confirmar=True):
        self._u = Umbrales(confirmar_mutaciones=confirmar)

    async def cargar(self, empresa_id):
        return self._u


class _FakeDispatcher:
    """Registra el ToolCall recibido sin ejecutar (para el corte match/ejecución)."""

    def __init__(self, resultado):
        self._res = resultado
        self.recibido = None

    def exponer_catalogo(self, ctx):
        return []

    async def ejecutar(self, tool_call, ctx, recursos):
        self.recibido = (tool_call, ctx, recursos)
        return self._res


class _NoConfig:
    async def overrides(self, empresa_id):
        return {}


class _NoKey:
    async def api_key(self, empresa_id, provider):
        return None


# --------------------------------- helpers --------------------------------

@dataclass
class _Setup:
    bypass: Bypass
    dispatcher: Dispatcher
    recursos: Recursos
    repo: _VentasRepo
    cat_precios: _CatalogoPrecios
    cat_bypass: _CatalogoBypass


def _setup(productos, *, confirmar=True) -> _Setup:
    repo = _VentasRepo(productos)
    deps = Deps(ventas=VentaService(repo), caja=None, fiados=None, clientes=None)
    cat_precios = _CatalogoPrecios(productos)
    recursos = Recursos(deps=deps, catalogo=cat_precios, umbrales=_Umbrales(confirmar))
    dispatcher = Dispatcher(config_store=_NoConfig(), key_store=_NoKey(), plataforma=None)
    cat_bypass = _CatalogoBypass(productos)
    return _Setup(Bypass(cat_bypass, dispatcher), dispatcher, recursos, repo, cat_precios, cat_bypass)


def _ctx(key="idem-1", confirmado=False) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=5, rol="vendedor", origen="bot",
        idempotency_key=key, capacidades=frozenset({"ventas", "caja"}), confirmado=confirmado,
    )


# ------------------- #1 corte match / ejecución ---------------------------

async def test_match_emite_toolcall_normalizado_y_no_llama_servicio():
    s = _setup([VINILO])
    disp = _FakeDispatcher(Resultado(data={}, resumen="Venta #1", evento="venta_registrada"))
    bypass = Bypass(s.cat_bypass, disp)

    res = await bypass.intentar("3 vinilo", _ctx(), s.recursos)

    assert isinstance(res, Resultado)
    tool_call, _ctx_pasado, _rec = disp.recibido
    assert tool_call.name == "registrar_venta"
    assert tool_call.arguments["metodo_pago"] == "efectivo"
    assert tool_call.arguments["items"] == [{"producto_id": 7, "cantidad": Decimal("3")}]
    assert s.repo.creadas == 0          # no se llamó a VentaService directamente


# ------------------- #2 no-match → CaeAlModelo ----------------------------

@pytest.mark.parametrize("frase", [
    "fiado 2 vinilo",          # cliente/crédito
    "cuanto vale el vinilo",   # consulta
    "cambia el precio",        # modificación
    "2 taladro",               # producto no exacto (no está en catálogo)
])
async def test_no_match_cae_al_modelo_no_ejecuta(frase):
    s = _setup([VINILO, PUNTILLAS])
    disp = _FakeDispatcher(Resultado(data={}, resumen="x"))
    bypass = Bypass(s.cat_bypass, disp)

    assert await bypass.intentar(frase, _ctx(), s.recursos) is None
    assert disp.recibido is None       # no ejecutó nada


async def test_multiproducto_all_or_nothing():
    # Multi-producto (coma/salto): si TODOS los ítems resuelven, registra UNA venta con todas las
    # líneas; si alguno no, defiere TODO (no registra parcial). Anti-alucinación all-or-nothing.
    s = _setup([VINILO, PUNTILLAS])
    res = await s.bypass.intentar("2 vinilo, 3 puntillas", _ctx(key="multi-ok"), s.recursos)
    assert isinstance(res, Resultado)
    items = {(l.producto_id, l.cantidad) for l in s.repo.ultimo_header.lineas}
    assert items == {(7, Decimal("2")), (9, Decimal("3"))}

    # Un ítem inexistente → defiere todo, NO registra el otro.
    s2 = _setup([VINILO, PUNTILLAS])
    res2 = await s2.bypass.intentar("2 vinilo, 5 taladro", _ctx(key="multi-no"), s2.recursos)
    assert res2 is None
    assert s2.repo.creadas == 0


async def test_escalonado_lo_resuelve_el_motor():
    # Mayorista por umbral: el bypass ya NO difiere; el motor de precios computa bajo/sobre umbral
    # de forma determinista (precios.py). cemento: umbral=10, bajo=20000, sobre=18000.
    s = _setup([ESCALONADO])
    res = await s.bypass.intentar("3 cemento", _ctx(key="esc-bajo"), s.recursos)
    assert isinstance(res, Resultado)                         # registrada por el bypass, no diferida
    assert s.repo.ultimo_header.total == Decimal("60000.00")  # 3 < umbral(10) → bajo 20000 ×3

    s2 = _setup([ESCALONADO])
    res2 = await s2.bypass.intentar("12 cemento", _ctx(key="esc-sobre"), s2.recursos)
    assert isinstance(res2, Resultado)
    assert s2.repo.ultimo_header.total == Decimal("216000.00")  # 12 >= umbral(10) → sobre 18000 ×12


async def test_granel_grm_registra_por_gramo_no_millones():
    # "500 puntilla 1 sin cabeza" = 500 g = 1 caja → $7000 (NO 500 × 7000 = 3.5M). Anti-alucinación:
    # el bypass registra el total exacto del granel, sin sobre-registro grosero.
    s = _setup([PUNTILLA_GRM])
    res = await s.bypass.intentar("500 puntilla 1 sin cabeza", _ctx(key="grm-1"), s.recursos)
    assert isinstance(res, Resultado)
    assert s.repo.ultimo_header.total == Decimal("7000.00")


async def test_granel_cms_registra_por_centimetro():
    # "30 lija esmeril n36" = 30 cm → 30 × (22000/100) = $6600 (NO 30 × 22000 = 660k).
    s = _setup([LIJA_ESMERIL])
    res = await s.bypass.intentar("30 lija esmeril n36", _ctx(key="cms-1"), s.recursos)
    assert isinstance(res, Resultado)
    assert s.repo.ultimo_header.total == Decimal("6600.00")


async def test_granel_grm_modo_caja_es_paquete_no_gramos():
    # "2 cajas puntilla..." = 2 PAQUETES (la presentación normal), no 2 gramos: 2 × precio_caja.
    s = _setup([PUNTILLA_GRM])               # precio caja (500 g) = 7000
    res = await s.bypass.intentar("2 cajas puntilla 1 sin cabeza", _ctx(key="caja-2"), s.recursos)
    assert isinstance(res, Resultado)
    assert s.repo.ultimo_header.total == Decimal("14000.00")


async def test_granel_grm_media_caja():
    s = _setup([PUNTILLA_GRM])
    res = await s.bypass.intentar("media caja puntilla 1 sin cabeza", _ctx(key="caja-media"), s.recursos)
    assert isinstance(res, Resultado)
    assert s.repo.ultimo_header.total == Decimal("3500.00")   # 0.5 caja → 250 g → 0.5 × 7000


async def test_para_en_nombre_de_producto_resuelve_no_es_cliente():
    # "Broca para Muro" lleva "para Muro" (palabra capitalizada) en el NOMBRE: no es un cliente.
    # El bypass reintenta catalog-aware y registra el producto.
    s = _setup([BROCA_MURO])
    res = await s.bypass.intentar("1 Broca para Muro 1/4", _ctx(key="para-prod"), s.recursos)
    assert isinstance(res, Resultado)
    assert s.repo.ultimo_header.total == Decimal("5000.00")


async def test_para_cliente_real_sigue_difiriendo():
    # "para Pedro" NO es un producto: el slug no resuelve → defiere al modelo (que enlaza el cliente).
    s = _setup([VINILO, BROCA_MURO])
    res = await s.bypass.intentar("2 vinilo para Pedro", _ctx(key="para-cli"), s.recursos)
    assert res is None
    assert s.repo.creadas == 0


async def test_fraccion_inexistente_en_catalogo_cae_al_modelo():
    s = _setup([LIJA])               # lija sin fracciones configuradas
    disp = _FakeDispatcher(Resultado(data={}, resumen="x"))
    bypass = Bypass(s.cat_bypass, disp)
    assert await bypass.intentar("1/4 lija", _ctx(), s.recursos) is None
    assert disp.recibido is None


# ------------------- #3 paridad directo-vs-despachador --------------------

# (frase, productos, líneas esperadas [(producto_id, cantidad)], total gold FerreBot)
_PARIDAD = [
    ("3 vinilo", [VINILO], [(7, Decimal("3"))], Decimal("60000.00")),                       # enteros
    ("2 1/2 manguera", [MANGUERA], [(8, Decimal("2")), (8, Decimal("0.5"))], Decimal("21000.00")),  # fracción mixta
    ("1/2 puntillas", [PUNTILLAS], [(9, Decimal("0.5"))], Decimal("1600.00")),              # por peso (media libra)
    ("4 tornillos caja", [CAJA], [(10, Decimal("4"))], Decimal("100000.00")),               # por caja
]


@pytest.mark.parametrize("frase, productos, lineas_esp, total_esp", _PARIDAD)
async def test_paridad_directo_vs_despachador(frase, productos, lineas_esp, total_esp):
    # Convergente: bypass → dispatcher.ejecutar → VentaService
    s = _setup(productos)
    res = await s.bypass.intentar(frase, _ctx(key="conv"), s.recursos)
    assert isinstance(res, Resultado)
    h_conv = s.repo.ultimo_header

    # Directo: VentaService con las líneas que el bypass debe producir
    repo_dir = _VentasRepo(productos)
    datos = VentaCrear(
        metodo_pago="efectivo", origen="bot", idempotency_key="dir",
        lineas=[VentaDetalleCrear(producto_id=pid, cantidad=c) for pid, c in lineas_esp],
    )
    await VentaService(repo_dir).registrar_venta(datos, vendedor_id=5)
    h_dir = repo_dir.ultimo_header

    # gold FerreBot + identidad directo == convergente
    assert h_conv.total == total_esp
    assert h_dir.total == total_esp
    assert (h_conv.subtotal, h_conv.impuestos, h_conv.total) == (h_dir.subtotal, h_dir.impuestos, h_dir.total)
    assert [(l.producto_id, l.cantidad) for l in h_conv.lineas] == [(l.producto_id, l.cantidad) for l in h_dir.lineas]
    # fidelidad: la cantidad llega como Decimal por el ToolCall
    assert all(isinstance(l.cantidad, Decimal) for l in h_conv.lineas)


# ------------------- #4 R1/R2 inertes + idempotencia + R3 -----------------

async def test_venta_por_bypass_R1_R2_inertes():
    s = _setup([VINILO], confirmar=True)   # aunque confirmar esté ON, la venta no es confirmable
    res = await s.bypass.intentar("3 vinilo", _ctx(), s.recursos)
    assert isinstance(res, Resultado)               # R1 no cortó (producto resuelto → candidatos=1)
    (linea,) = s.repo.ultimo_header.lineas
    assert linea.precio_unitario == Decimal("20000")  # R2 no corrió: sin precio_unitario, catálogo manda
    assert linea.total_linea == Decimal("60000.00")
    assert s.repo.creadas == 1


async def test_idempotencia_venta_por_bypass_reusa_key_no_duplica():
    s = _setup([VINILO])
    ctx = _ctx(key="misma-key")
    r1 = await s.bypass.intentar("3 vinilo", ctx, s.recursos)
    r2 = await s.bypass.intentar("3 vinilo", ctx, s.recursos)   # reintento / el "sí" reusa la key
    assert isinstance(r1, Resultado) and r1.idempotente == "aplicada"
    assert isinstance(r2, Resultado) and r2.idempotente == "duplicada"
    assert s.repo.creadas == 1                      # no se duplicó


async def test_sink_dispatcher_gasto_R3_confirma_y_reusa_key():
    # El sink al que converge el bypass aplica R3 + idempotencia a una mutación confirmable.
    class _CajaRepo:
        def __init__(self):
            self._por_key = {}
            self.insertados = 0

        async def caja_abierta(self, usuario_id, lock=False):
            return SimpleNamespace(id=1)

        async def gasto_por_key(self, key):
            return self._por_key.get(key)

        async def insertar_gasto(self, *, caja_id, usuario_id, categoria, monto, concepto, idempotency_key):
            self.insertados += 1
            g = SimpleNamespace(id=self.insertados)
            if idempotency_key:
                self._por_key[idempotency_key] = g
            return g

    caja_repo = _CajaRepo()
    deps = Deps(ventas=None, caja=CajaService(caja_repo), fiados=None, clientes=None)
    recursos = Recursos(deps=deps, catalogo=_CatalogoPrecios([]), umbrales=_Umbrales(confirmar=True))
    disp = Dispatcher(config_store=_NoConfig(), key_store=_NoKey(), plataforma=None)
    tc = ToolCall(id="g", name="registrar_gasto",
                  arguments={"categoria": "transporte", "monto": Decimal("15000"), "concepto": "flete"})

    # sin confirmar → R3 corta, no ejecuta
    r_conf = await disp.ejecutar(tc, _ctx(key="g1", confirmado=False), recursos)
    assert isinstance(r_conf, Confirmar)
    assert caja_repo.insertados == 0

    # con confirmado → ejecuta
    r_ok = await disp.ejecutar(tc, _ctx(key="g1", confirmado=True), recursos)
    assert isinstance(r_ok, Resultado)
    assert caja_repo.insertados == 1

    # el "sí" repetido reusa la misma key → no duplica
    r_dup = await disp.ejecutar(tc, _ctx(key="g1", confirmado=True), recursos)
    assert isinstance(r_dup, Resultado) and r_dup.idempotente == "duplicada"
    assert caja_repo.insertados == 1


# ------------------- #5 doble lectura (decisión #5b) ----------------------

async def test_convergencia_no_agrega_lecturas_a_postgres():
    s = _setup([VINILO])
    await s.bypass.intentar("3 vinilo", _ctx(), s.recursos)
    assert s.cat_bypass.lecturas == 1        # el bypass resolvió el producto una sola vez
    assert s.cat_precios.obtener_llamadas == 0   # R1 NO releyó: usó recursos.resueltos (decisión #5b)
