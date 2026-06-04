"""Paridad del bypass: misma entrada que FerreBot → mismo intent/efecto (ferrebot-logica-portar.md §2).

Dos niveles:
  1. Parser puro (`analizar`/`normalizar_slug`): normalización, mapa de fracciones (numéricas y
     escritas), descomposición de cantidad y deshabilitadores. Sin BD.
  2. Orquestador (`Bypass`): cablea el intent al MISMO `VentaService` (su `VentasRepo` es un
     Protocol, aquí falseado) — sin reimplementar precios. Mixta = línea entera (simple) + línea
     fracción, así reproduce `precio_unidad×1 + fraccion[½]` exacto.
"""
from decimal import Decimal

import pytest

from ai.bypass import (
    Bypass,
    CaeAlModelo,
    ProductoBypass,
    VentaSimple,
    analizar,
    normalizar_slug,
)
from core.config.timezone import now_co
from modules.inventario.precios import EsquemaPrecio, FraccionPrecio
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import ProductoPrecio, VentaService


# --------------------------- 1. Parser puro -------------------------------

@pytest.mark.parametrize(
    "texto, producto, componentes",
    [
        ("2 martillo", "martillo", (Decimal("2"),)),
        ("3 vinilo", "vinilo", (Decimal("3"),)),
        ("1/2 vinilo azul t1", "vinilo azul t1", (Decimal("0.5"),)),
        ("1/4 lija", "lija", (Decimal("0.25"),)),
        ("1-1/2 vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
        ("1 1/2 vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
        ("medio vinilo", "vinilo", (Decimal("0.5"),)),
        ("tres cuartos vinilo", "vinilo", (Decimal("0.75"),)),
        ("1 y medio vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
    ],
)
def test_analiza_ventas_simples(texto, producto, componentes):
    res = analizar(texto)
    assert isinstance(res, VentaSimple)
    assert res.producto == producto
    assert res.componentes == componentes


@pytest.mark.parametrize(
    "texto",
    [
        "fiado 2 martillo",          # crédito
        "2 martillo a nombre de pedro",
        "abono 5000 a juan",
        "2 martillo para Juan",      # para <Nombre propio> (mayúscula en original)
        "cuanto vale el martillo",   # consulta
        "hay stock de vinilo",
        "cambia el precio del martillo",  # modificación
        "2 martillo, 3 puntillas",   # multi-producto
        "2 martillo\n3 puntillas",
        "martillo",                  # sin cantidad → ambiguo
        "hola",
        "",
    ],
)
def test_deshabilitadores_caen_al_modelo(texto):
    assert isinstance(analizar(texto), CaeAlModelo)


def test_para_minuscula_no_deshabilita():
    # "para reja" (sustantivo común, minúscula) no es un cliente: sigue siendo bypass.
    res = analizar("2 tornillo para madera")
    assert isinstance(res, VentaSimple)


@pytest.mark.parametrize(
    "crudo, slug",
    [
        ("Lija #120", "lija n120"),
        ("Martillo Truper", "martillo truper"),
        ("Válvula 1/2 Roja", "valvula 1 2 roja"),
        ("CAÑO ½", "cano 1 2"),   # ½ (U+00BD) se descompone NFKD en 1⁄2
    ],
)
def test_normalizar_slug(crudo, slug):
    assert normalizar_slug(crudo) == slug


# --------------------------- 2. Orquestador -------------------------------

class _FakeVentasRepo:
    """Implementa el Protocol VentasRepo de modules.ventas.service (repo falso)."""

    def __init__(self, producto: ProductoPrecio, stock: Decimal):
        self._producto = producto
        self._stock = stock
        self._consecutivo = 0
        self.ultimo_header = None

    async def buscar_por_idempotency(self, key):
        return None

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


class _FakeCatalogo:
    def __init__(self, productos: dict[str, ProductoBypass]):
        self._por_slug = productos

    async def producto_exacto(self, slug):
        return self._por_slug.get(slug)


_VINILO_ESQUEMA = EsquemaPrecio(
    precio_venta=Decimal("20000"),
    fracciones=(FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("12000")),),
)
_VINILO_PROD = ProductoPrecio(
    id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True,
    fracciones=(FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("12000")),),
)


def _bypass(prod_precio, esquema, stock=Decimal("100"), slug="vinilo"):
    repo = _FakeVentasRepo(prod_precio, stock)
    catalogo = _FakeCatalogo({slug: ProductoBypass(id=prod_precio.id, nombre=slug, esquema=esquema)})
    return Bypass(catalogo, VentaService(repo)), repo


async def test_entero_mismo_efecto_que_servicio():
    by, repo = _bypass(_VINILO_PROD, _VINILO_ESQUEMA)
    res = await by.intentar("3 vinilo", vendedor_id=1)
    assert res is not None and res.replay is False
    assert res.venta.total == Decimal("60000.00")        # 3 × 20000, IVA 0
    assert len(repo.ultimo_header.lineas) == 1


async def test_mixta_descompone_entero_mas_fraccion():
    # 1-1/2 vinilo = línea(1, simple)=20000 + línea(0.5, fracción)=12000 = 32000.
    by, repo = _bypass(_VINILO_PROD, _VINILO_ESQUEMA)
    res = await by.intentar("1-1/2 vinilo", vendedor_id=1)
    assert res is not None
    assert res.venta.total == Decimal("32000.00")
    assert len(repo.ultimo_header.lineas) == 2


async def test_deshabilitador_no_llama_al_servicio():
    by, _ = _bypass(_VINILO_PROD, _VINILO_ESQUEMA)
    assert await by.intentar("fiado 2 vinilo", vendedor_id=1) is None


async def test_producto_no_encontrado_cae_al_modelo():
    by, _ = _bypass(_VINILO_PROD, _VINILO_ESQUEMA)
    assert await by.intentar("2 taladro", vendedor_id=1) is None


async def test_precio_escalonado_cae_al_modelo():
    # Producto mayorista (precio por umbral) → el bypass nunca lo resuelve.
    escalonado = EsquemaPrecio(
        precio_venta=Decimal("20000"), precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("20000"), precio_sobre_umbral=Decimal("18000"),
    )
    prod = ProductoPrecio(
        id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True,
        precio_umbral=Decimal("10"), precio_bajo_umbral=Decimal("20000"),
        precio_sobre_umbral=Decimal("18000"),
    )
    by, _ = _bypass(prod, escalonado)
    assert await by.intentar("3 vinilo", vendedor_id=1) is None


async def test_fraccion_inexistente_cae_al_modelo():
    # El catálogo del producto no tiene fracción 1/4 → al modelo.
    sin_frac = EsquemaPrecio(precio_venta=Decimal("20000"))
    prod = ProductoPrecio(id=7, nombre="vinilo", precio_venta=Decimal("20000"), iva=0, activo=True)
    by, _ = _bypass(prod, sin_frac)
    assert await by.intentar("1/4 vinilo", vendedor_id=1) is None
