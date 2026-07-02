"""Despachador IA (ADR 0005): filtrado de catálogo, RBAC/capacidades, rieles, idempotencia, paridad.

Todo con fakes (repos y puertos falseados, proveedor mockeado): núcleo determinista, sin BD ni
claves reales. La paridad demuestra la regla dura del ADR: bypass y tool-calling convergen en el
MISMO `VentaService`. Los rieles `Preguntar`/`Confirmar` cortan SIN mutar nada.
"""
from decimal import Decimal

import pytest

from ai.dispatcher import Dispatcher, Recursos, catalogo_visible
from ai.envelope import Contexto, ErrorTool, Resultado
from ai.ports import ProductoCatalogo, Umbrales
from ai.rieles import Confirmar, Preguntar
from ai.tools import Deps
from core.config.timezone import now_co
from core.llm.base import ToolCall
from core.llm.factory import PlataformaLLM, Turno
from modules.caja.models import Caja, Gasto
from modules.caja.service import CajaService
from modules.clientes.models import Cliente
from modules.clientes.service import ClientesService
from modules.fiados.models import Fiado
from modules.fiados.service import FiadosService
from modules.inventario.precios import EsquemaPrecio
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import ProductoPrecio, VentaService


# --------------------------- Fakes ----------------------------------------
class _FakeVentasRepo:
    def __init__(self, producto: ProductoPrecio, stock=Decimal("100"), existente: VentaLeer | None = None):
        self._producto = producto
        self._stock = stock
        self._existente = existente
        self._consecutivo = 0
        self.ultimo_header = None

    async def buscar_por_idempotency(self, key):
        return self._existente

    async def obtener_producto(self, producto_id):
        return self._producto if producto_id == self._producto.id else None

    async def lock_inventario(self, producto_id):
        return self._stock

    async def siguiente_consecutivo(self):
        self._consecutivo += 1
        return self._consecutivo

    async def crear_venta(self, header):
        self.ultimo_header = header
        return VentaLeer(
            id=1, consecutivo=header.consecutivo, cliente_id=header.cliente_id,
            vendedor_id=header.vendedor_id, fecha=now_co(), subtotal=header.subtotal,
            impuestos=header.impuestos, total=header.total, metodo_pago=header.metodo_pago,
            estado="completada", origen=header.origen, idempotency_key=header.idempotency_key,
        )


class _FakeCajaRepo:
    def __init__(self, abierta=True):
        self._abierta = abierta
        self.gasto_insertado = None

    async def caja_abierta(self, usuario_id, lock=False):
        if not self._abierta:
            return None
        return Caja(id=20, usuario_id=usuario_id, fecha_apertura=now_co(),
                    saldo_inicial=Decimal("0"), estado="abierta")

    async def gasto_por_key(self, key):
        return None

    async def insertar_gasto(self, **kw):
        self.gasto_insertado = kw
        return Gasto(id=77, categoria=kw["categoria"], monto=kw["monto"], creado_en=now_co())


class _FakeFiadosRepo:
    def __init__(self):
        self.creado = None

    async def lock_cliente(self, cliente_id):
        return Cliente(id=cliente_id, nombre="X", saldo_fiado=Decimal("0"))

    async def fiado_por_key(self, key):
        return None

    async def crear_fiado(self, *, cliente_id, venta_id, monto, idempotency_key):
        self.creado = (cliente_id, monto)
        return Fiado(id=5, cliente_id=cliente_id, venta_id=venta_id, monto=monto, saldo=monto)


class _FakeClientesRepo:
    async def buscar_por_documento(self, documento):
        return None

    async def crear(self, datos):
        return Cliente(id=57, nombre=datos.nombre, documento=datos.documento, saldo_fiado=Decimal("0"))


class _FakeCatalogo:
    def __init__(self, productos: dict[int, ProductoCatalogo]):
        self._p = productos

    async def obtener(self, producto_id):
        return self._p.get(producto_id)


class _FakeUmbrales:
    def __init__(self, umbrales: Umbrales):
        self._u = umbrales

    async def cargar(self, empresa_id):
        return self._u


# --------------------------- Datos comunes --------------------------------
_PROD = ProductoPrecio(id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True)
_CAT = ProductoCatalogo(id=7, nombre="vinilo", activo=True, esquema=EsquemaPrecio(precio_venta=Decimal("20000")))


def _recursos(*, ventas_repo=None, caja_abierta=True, umbrales=None, catalogo=None):
    vrepo = ventas_repo or _FakeVentasRepo(_PROD)
    deps = Deps(
        ventas=VentaService(vrepo),
        caja=CajaService(_FakeCajaRepo(abierta=caja_abierta)),
        fiados=FiadosService(_FakeFiadosRepo()),
        clientes=ClientesService(_FakeClientesRepo()),
    )
    rec = Recursos(
        deps=deps,
        catalogo=_FakeCatalogo(catalogo if catalogo is not None else {7: _CAT}),
        umbrales=_FakeUmbrales(umbrales or Umbrales()),
    )
    return rec, vrepo


def _ctx(rol="vendedor", capacidades=frozenset({"ventas", "caja", "fiados"}), confirmado=False, key=None):
    return Contexto(
        tenant_id=1, usuario_id=1, rol=rol, origen="bot",
        idempotency_key=key, capacidades=capacidades, confirmado=confirmado,
    )


def _disp():
    return Dispatcher(
        config_store=_FakeConfigStore(), key_store=_FakeKeyStore(),
        plataforma=PlataformaLLM(provider="openai", model_worker="gpt-4o-mini",
                                 model_orquestador="gpt-4o", keys={"openai": "sk-test"}),
    )


class _FakeConfigStore:
    async def overrides(self, empresa_id):
        return {}


class _FakeKeyStore:
    async def api_key(self, empresa_id, provider):
        return None


# --------------------------- Catálogo expuesto (RBAC + capacidades) -------
def test_catalogo_oculta_fiados_si_no_hay_capacidad():
    visibles = {t.name for t in _disp().exponer_catalogo(_ctx(capacidades=frozenset({"ventas", "caja"})))}
    assert "registrar_venta" in visibles and "crear_cliente" in visibles
    assert "registrar_fiado" not in visibles and "abonar_fiado" not in visibles


def test_catalogo_incluye_fiados_con_capacidad():
    visibles = {t.name for t in _disp().exponer_catalogo(_ctx(capacidades=frozenset({"ventas", "fiados"})))}
    assert {"registrar_fiado", "abonar_fiado"} <= visibles


def test_catalogo_oculta_venta_y_gasto_sin_features_finas():
    # ADR 0021: sin `ventas`/`caja` (tenant de servicios puro) el modelo NO ve las tools contables.
    visibles = {t.name for t in _disp().exponer_catalogo(_ctx(capacidades=frozenset()))}
    assert "registrar_venta" not in visibles and "registrar_gasto" not in visibles
    assert "consultar_producto" not in visibles and "consultar_ventas_dia" not in visibles
    assert "crear_cliente" in visibles                       # núcleo: siempre


def test_catalogo_con_metapack_pos_ve_todo_el_retail():
    visibles = {t.name for t in _disp().exponer_catalogo(_ctx(capacidades=frozenset({"pos"})))}
    assert {"registrar_venta", "registrar_gasto", "consultar_producto", "consultar_ventas_dia"} <= visibles


def test_catalogo_visible_helper_filtra_por_rol():
    # Todas las herramientas del alcance son rol vendedor → admin las ve todas también.
    assert len(catalogo_visible(_ctx(rol="admin"))) == len(catalogo_visible(_ctx()))


# --------------------------- RBAC / capacidad / validación ----------------
async def test_capacidad_no_habilitada_corta():
    rec, _ = _recursos()
    tc = ToolCall(id="1", name="registrar_fiado", arguments={"cliente_id": 1, "monto": 1000})
    res = await _disp().ejecutar(tc, _ctx(capacidades=frozenset()), rec)
    assert isinstance(res, ErrorTool) and res.error == "capacidad_no_habilitada" and res.recuperable is False


async def test_args_invalidos_son_validacion_recuperable():
    rec, _ = _recursos()
    tc = ToolCall(id="1", name="registrar_gasto", arguments={"monto": -5})  # falta categoria, monto<=0
    res = await _disp().ejecutar(tc, _ctx(), rec)
    assert isinstance(res, ErrorTool) and res.error == "validacion" and res.recuperable is True


async def test_herramienta_desconocida():
    rec, _ = _recursos()
    res = await _disp().ejecutar(ToolCall(id="1", name="borrar_todo", arguments={}), _ctx(), rec)
    assert isinstance(res, ErrorTool) and res.error == "error_interno"


async def test_item_varia_malformado_es_validacion_no_revienta():
    # Sin producto_id y sin descripcion/precio: lo captura la validación de args, no el handler.
    rec, vrepo = _recursos()
    tc = ToolCall(id="1", name="registrar_venta",
                  arguments={"items": [{"cantidad": 1}], "metodo_pago": "efectivo"})
    res = await _disp().ejecutar(tc, _ctx(), rec)
    assert isinstance(res, ErrorTool) and res.error == "validacion" and res.recuperable is True
    assert vrepo.ultimo_header is None


# --------------------------- Riel 1: producto -----------------------------
async def test_riel_producto_desconocido_no_registra():
    rec, vrepo = _recursos(catalogo={})  # catálogo vacío → producto 7 no resuelve
    tc = ToolCall(id="1", name="registrar_venta",
                  arguments={"items": [{"producto_id": 7, "cantidad": 1}], "metodo_pago": "efectivo"})
    res = await _disp().ejecutar(tc, _ctx(), rec)
    assert isinstance(res, Preguntar) and res.codigo == "producto_no_encontrado"
    assert vrepo.ultimo_header is None  # no mutó


# --------------------------- Riel 2: precio dudoso ------------------------
async def test_riel_precio_dudoso_no_registra():
    rec, vrepo = _recursos()
    tc = ToolCall(id="1", name="registrar_venta", arguments={
        "items": [{"producto_id": 7, "cantidad": 1, "precio_unitario": 25000,
                   "precio_dicho_por_usuario": False}],
        "metodo_pago": "efectivo",
    })
    res = await _disp().ejecutar(tc, _ctx(), rec)
    assert isinstance(res, Preguntar) and res.codigo == "precio_dudoso"
    assert vrepo.ultimo_header is None


async def test_riel_precio_declarado_si_registra_con_override():
    rec, vrepo = _recursos()
    tc = ToolCall(id="1", name="registrar_venta", arguments={
        "items": [{"producto_id": 7, "cantidad": 1, "precio_unitario": 25000,
                   "precio_dicho_por_usuario": True}],
        "metodo_pago": "efectivo",
    })
    res = await _disp().ejecutar(tc, _ctx(), rec)
    assert isinstance(res, Resultado)
    assert vrepo.ultimo_header.total == Decimal("25000.00")  # respetó el precio declarado


# --------------------------- Riel 3: confirmación -------------------------
async def test_gasto_sin_confirmar_pide_confirmacion():
    rec, _ = _recursos()
    tc = ToolCall(id="1", name="registrar_gasto",
                  arguments={"categoria": "transporte", "monto": 15000})
    res = await _disp().ejecutar(tc, _ctx(confirmado=False), rec)
    assert isinstance(res, Confirmar) and "15000" in res.resumen


async def test_gasto_confirmado_ejecuta():
    rec, _ = _recursos()
    tc = ToolCall(id="1", name="registrar_gasto",
                  arguments={"categoria": "transporte", "monto": 15000})
    res = await _disp().ejecutar(tc, _ctx(confirmado=True), rec)
    assert isinstance(res, Resultado) and res.evento == "gasto_registrado"


async def test_confirmacion_desactivada_por_umbral_ejecuta_directo():
    rec, _ = _recursos(umbrales=Umbrales(confirmar_mutaciones=False))
    tc = ToolCall(id="1", name="registrar_gasto",
                  arguments={"categoria": "transporte", "monto": 15000})
    res = await _disp().ejecutar(tc, _ctx(confirmado=False), rec)
    assert isinstance(res, Resultado)


# --------------------------- Idempotencia ---------------------------------
async def test_venta_replay_marca_duplicada():
    previa = VentaLeer(
        id=1, consecutivo=1, cliente_id=None, vendedor_id=1, fecha=now_co(),
        subtotal=Decimal("20000"), impuestos=Decimal("0"), total=Decimal("20000.00"),
        metodo_pago="efectivo", estado="completada", origen="bot", idempotency_key="k1",
    )
    rec, _ = _recursos(ventas_repo=_FakeVentasRepo(_PROD, existente=previa))
    tc = ToolCall(id="1", name="registrar_venta",
                  arguments={"items": [{"producto_id": 7, "cantidad": 1}], "metodo_pago": "efectivo"})
    res = await _disp().ejecutar(tc, _ctx(key="k1"), rec)
    assert isinstance(res, Resultado) and res.idempotente == "duplicada"


# Nota: la paridad bypass ↔ tool-call vive ahora en test_bypass_convergencia.py — al converger el
# bypass por dispatcher.ejecutar, ambos caminos son el MISMO; ya no se prueba aquí por separado.


# --------------------------- Selección de proveedor (get_llm) -------------
async def test_seleccionar_proveedor_via_get_llm():
    resuelto = await _disp().seleccionar_proveedor(1, turno=Turno.WORKER)
    assert resuelto.provider_nombre == "openai"
    assert resuelto.model == "gpt-4o-mini"      # default worker de plataforma
    assert resuelto.provider.api_key == "sk-test"
