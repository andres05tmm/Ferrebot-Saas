"""Cableado compartido del harness de evals del agente (función-call + aislamiento).

Reusa los MISMOS fakes que `tests/test_bypass_convergencia.py` (servicios de dominio sin BD), de
modo que la evaluación corre en milisegundos y sin Postgres. La capa de datos sigue entrando solo
por repositorios (aquí, fakes que cumplen el contrato del repo); el despachador real es el de
producción (`ai.dispatcher.Dispatcher`), no un doble.

Dos planos de evaluación se arman desde aquí:

  - Determinista (función-call): catálogo fijo en memoria → `Bypass.intentar` / `Dispatcher.ejecutar`
    devuelven exactamente la herramienta y los args esperados, sin tocar la BD.
  - Aislamiento multi-tenant (en `test_aislamiento.py`): usa las bases efímeras reales de
    `conftest.py` (no este módulo) para probar que una herramienta de la empresa A nunca escribe en B.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

from ai.bypass import Bypass, ProductoBypass, normalizar_slug
from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto
from ai.ports import ProductoCatalogo, Umbrales
from ai.tools import Deps
from core.config.timezone import now_co
from modules.caja.service import CajaService
from modules.inventario.precios import FraccionPrecio
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import ProductoPrecio, VentaService


# --- Catálogo fijo de evaluación (espeja productos típicos de mostrador) ------
def _frac(decimal: str, total: str) -> FraccionPrecio:
    return FraccionPrecio(decimal=Decimal(decimal), precio_total=Decimal(total))


# IVA 0 a propósito: la corrección de totales/IVA ya la cubre `test_bypass_convergencia.py`; aquí el
# foco es la precisión de la LLAMADA (herramienta + args), no la aritmética de precios.
VINILO = ProductoPrecio(id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True,
                        fracciones=(_frac("0.5", "12000"),))
MANGUERA = ProductoPrecio(id=8, nombre="manguera", precio_venta=Decimal("8000"), iva=0, activo=True,
                          fracciones=(_frac("0.5", "5000"),))
PUNTILLA = ProductoPrecio(id=9, nombre="puntilla", precio_venta=Decimal("3000"), iva=0, activo=True,
                          fracciones=(_frac("0.5", "1600"),))    # por libra: media libra
TORNILLOS_CAJA = ProductoPrecio(id=10, nombre="tornillos caja", precio_venta=Decimal("25000"),
                                iva=0, activo=True)
CEMENTO = ProductoPrecio(id=11, nombre="cemento", precio_venta=Decimal("20000"), iva=0, activo=True,
                         precio_umbral=Decimal("10"), precio_bajo_umbral=Decimal("20000"),
                         precio_sobre_umbral=Decimal("18000"))   # escalonado → cae al modelo
LIJA = ProductoPrecio(id=12, nombre="lija", precio_venta=Decimal("2000"), iva=0, activo=True)  # sin fracc.
THINNER = ProductoPrecio(id=13, nombre="thinner", precio_venta=Decimal("30000"), iva=0, activo=True)
DRYWALL = ProductoPrecio(id=14, nombre="drywall", precio_venta=Decimal("18000"), iva=0, activo=True)

# Catálogo completo del plano determinista; los tests lo pasan entero a `construir`.
PRODUCTOS = (VINILO, MANGUERA, PUNTILLA, TORNILLOS_CAJA, CEMENTO, LIJA, THINNER, DRYWALL)
POR_NOMBRE = {p.nombre: p for p in PRODUCTOS}


# --- Fakes de repositorio (mismo contrato que los Sql*Repository, sin BD) -----
class VentasRepoFake:
    """Repo de ventas en memoria: resuelve productos, asigna consecutivos y guarda por idempotency."""

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


class CajaRepoFake:
    """Repo de caja en memoria (caja siempre abierta) para evaluar el handler de gasto."""

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


class CatalogoBypassFake:
    """`ai.bypass.CatalogoBypass`: resuelve slug → producto por coincidencia exacta única."""

    def __init__(self, productos):
        self._por_slug = {normalizar_slug(p.nombre): _pb(p) for p in productos}
        self.lecturas = 0

    async def producto_exacto(self, slug):
        self.lecturas += 1
        return self._por_slug.get(slug)


class CatalogoPreciosFake:
    """`ai.ports.CatalogoPrecios`: `obtener(id)` para los rieles del despachador."""

    def __init__(self, productos):
        self._por_id = {p.id: _pc(p) for p in productos}
        self.obtener_llamadas = 0

    async def obtener(self, pid):
        self.obtener_llamadas += 1
        return self._por_id.get(pid)


class UmbralesFake:
    def __init__(self, confirmar=True):
        self._u = Umbrales(confirmar_mutaciones=confirmar)

    async def cargar(self, empresa_id):
        return self._u


class NoConfig:
    async def overrides(self, empresa_id):
        return {}


class NoKey:
    async def api_key(self, empresa_id, provider):
        return None


def _pb(p: ProductoPrecio) -> ProductoBypass:
    return ProductoBypass(id=p.id, nombre=p.nombre, esquema=p.esquema())


def _pc(p: ProductoPrecio) -> ProductoCatalogo:
    return ProductoCatalogo(id=p.id, nombre=p.nombre, activo=p.activo, esquema=p.esquema())


# --- Composición: despachador real + recursos en memoria ----------------------
@dataclass
class Harness:
    bypass: Bypass
    dispatcher: Dispatcher
    recursos: Recursos
    ventas_repo: VentasRepoFake
    caja_repo: CajaRepoFake
    cat_bypass: CatalogoBypassFake
    cat_precios: CatalogoPreciosFake


def construir(productos=PRODUCTOS, *, confirmar=True) -> Harness:
    """Arma el despachador REAL con servicios de dominio en memoria (sin BD).

    `confirmar` simula `config_empresa.confirmar_mutaciones` (riel R3 de gasto/fiado/abono).
    """
    ventas_repo = VentasRepoFake(productos)
    caja_repo = CajaRepoFake()
    deps = Deps(
        ventas=VentaService(ventas_repo),
        caja=CajaService(caja_repo),
        fiados=None,        # fiado/abono cortan en RBAC/capacidad o R3 antes del handler
        clientes=None,
    )
    cat_precios = CatalogoPreciosFake(productos)
    recursos = Recursos(deps=deps, catalogo=cat_precios, umbrales=UmbralesFake(confirmar))
    dispatcher = Dispatcher(config_store=NoConfig(), key_store=NoKey(), plataforma=None)
    cat_bypass = CatalogoBypassFake(productos)
    return Harness(
        bypass=Bypass(cat_bypass, dispatcher),
        dispatcher=dispatcher,
        recursos=recursos,
        ventas_repo=ventas_repo,
        caja_repo=caja_repo,
        cat_bypass=cat_bypass,
        cat_precios=cat_precios,
    )


def ctx_eval(
    *, key="eval-1", confirmado=False, rol="vendedor",
    capacidades=frozenset({"ventas", "caja"}),   # contexto retail contable (ADR 0021)
) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=5, rol=rol, origen="bot",
        idempotency_key=key, capacidades=capacidades, confirmado=confirmado,
    )
