"""Unitarios del servicio de ventas: lógica pura con un repositorio falso (sin BD)."""
from decimal import Decimal

import pytest

from core.config.timezone import now_co
from modules.ventas.errors import LineaInvalida, ProductoNoEncontrado, StockInsuficiente
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear, VentaLeer
from modules.ventas.service import ProductoPrecio, VentaHeader, VentaService, calcular_totales


class FakeRepo:
    def __init__(self, *, productos=None, stock=None, existente=None):
        self.productos = productos or {}
        self.stock = stock or {}
        self.existente = existente
        self.creado: VentaHeader | None = None
        self._consec = 0

    async def buscar_por_idempotency(self, key):
        return self.existente

    async def obtener_producto(self, producto_id):
        return self.productos.get(producto_id)

    async def lock_inventario(self, producto_id):
        return self.stock.get(producto_id)

    async def siguiente_consecutivo(self):
        self._consec += 1
        return self._consec

    async def crear_venta(self, header: VentaHeader) -> VentaLeer:
        self.creado = header
        return VentaLeer(
            id=1, consecutivo=header.consecutivo, cliente_id=header.cliente_id,
            vendedor_id=header.vendedor_id, fecha=now_co(), subtotal=header.subtotal,
            impuestos=header.impuestos, total=header.total, metodo_pago=header.metodo_pago,
            estado="completada", origen=header.origen, idempotency_key=header.idempotency_key,
        )


def _producto(pid=1, precio="11900", iva=19, activo=True):
    return ProductoPrecio(id=pid, nombre="Martillo", precio_venta=Decimal(precio), iva=iva, activo=activo)


async def test_totales_iva_incluido():
    repo = FakeRepo(productos={1: _producto()}, stock={1: Decimal("100")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("2"))])
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)
    assert res.replay is False
    assert res.venta.total == Decimal("23800.00")
    assert res.venta.impuestos == Decimal("3800.00")   # IVA 19% incluido en 23800
    assert res.venta.subtotal == Decimal("20000.00")
    assert res.venta.subtotal + res.venta.impuestos == res.venta.total


async def test_idempotencia_devuelve_existente_sin_crear():
    previa = VentaLeer(
        id=99, consecutivo=5, cliente_id=None, vendedor_id=7, fecha=now_co(),
        subtotal=Decimal("100"), impuestos=Decimal("0"), total=Decimal("100"),
        metodo_pago="efectivo", estado="completada", origen="web", idempotency_key="abc",
    )
    repo = FakeRepo(existente=previa)
    datos = VentaCrear(
        metodo_pago="efectivo", idempotency_key="abc",
        lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("1"))],
    )
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)
    assert res.replay is True
    assert res.venta.id == 99
    assert repo.creado is None   # no se intentó crear de nuevo


async def test_stock_insuficiente():
    repo = FakeRepo(productos={1: _producto()}, stock={1: Decimal("1")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("2"))])
    with pytest.raises(StockInsuficiente):
        await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)


async def test_producto_no_encontrado():
    repo = FakeRepo(productos={}, stock={})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=42, cantidad=Decimal("1"))])
    with pytest.raises(ProductoNoEncontrado):
        await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)


async def test_producto_inactivo_no_se_vende():
    repo = FakeRepo(productos={1: _producto(activo=False)}, stock={1: Decimal("100")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("1"))])
    with pytest.raises(ProductoNoEncontrado):
        await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)


async def test_venta_varia_usa_precio_del_request():
    repo = FakeRepo()
    datos = VentaCrear(
        metodo_pago="efectivo",
        lineas=[VentaDetalleCrear(descripcion="Corte de llave", precio_unitario=Decimal("5000"), cantidad=Decimal("1"))],
    )
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)
    assert res.venta.total == Decimal("5000.00")
    assert res.venta.impuestos == Decimal("0.00")   # IVA 0 por defecto en varia
    assert repo.creado.lineas[0].descontar_stock is False


def test_varia_sin_precio_es_invalida_en_el_schema():
    # La validación de línea varia vive en el schema (Pydantic) antes de llegar al servicio.
    with pytest.raises(ValueError):
        VentaDetalleCrear(descripcion="x", cantidad=Decimal("1"))


def test_calcular_totales_multilinea():
    from modules.ventas.service import LineaResuelta
    lineas = [
        LineaResuelta(1, "A", Decimal("2"), Decimal("11900"), 19, Decimal("23800.00"), True),
        LineaResuelta(None, "Serv", Decimal("1"), Decimal("5000"), 0, Decimal("5000.00"), False),
    ]
    subtotal, impuestos, total = calcular_totales(lineas)
    assert total == Decimal("28800.00")
    assert impuestos == Decimal("3800.00")
    assert subtotal == Decimal("25000.00")
