"""Unitarios del servicio de ventas: lógica pura con un repositorio falso (sin BD)."""
from datetime import timedelta
from decimal import Decimal

import pytest

from core.config.timezone import now_co
from modules.ventas.errors import (
    LineaInvalida,
    OperacionNoAutorizada,
    ProductoNoEncontrado,
    StockInsuficiente,
    VentaConFacturaViva,
    VentaNoEncontrada,
    VentaNoEsDeHoy,
)
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

    async def obtener(self, venta_id):
        return None   # sin detalle: el guard de idempotencia compara solo la cabecera

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


def _producto(pid=1, precio="11900", iva=19, activo=True, **extra):
    return ProductoPrecio(
        id=pid, nombre="Martillo", precio_venta=Decimal(precio), iva=iva, activo=activo, **extra
    )


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


async def test_stock_insuficiente_solo_en_modo_estricto():
    """Con control_stock_estricto=ON (opt-in), vender más que el stock lanza StockInsuficiente."""
    repo = FakeRepo(productos={1: _producto()}, stock={1: Decimal("1")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("2"))])
    with pytest.raises(StockInsuficiente):
        await (VentaService(repo)).registrar_venta(datos, vendedor_id=7, control_stock_estricto=True)


async def test_stock_insuficiente_permisivo_por_defecto_no_bloquea():
    """Default PERMISIVO (flag OFF): vender más que el stock NO lanza; la línea descuenta stock igual."""
    repo = FakeRepo(productos={1: _producto()}, stock={1: Decimal("1")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("2"))])
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)   # sin pasar el flag
    assert res.replay is False
    assert repo.creado is not None and repo.creado.lineas[0].descontar_stock is True


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


async def test_linea_catalogo_usa_motor_escalonado():
    # Producto con precio escalonado: 12 >= umbral 10 → precio_sobre_umbral 4500.
    prod = _producto(
        precio="5000",
        precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("5000"),
        precio_sobre_umbral=Decimal("4500"),
    )
    repo = FakeRepo(productos={1: prod}, stock={1: Decimal("100")})
    datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("12"))])
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)
    assert res.venta.total == Decimal("54000.00")        # 4500 * 12
    assert repo.creado.lineas[0].precio_unitario == Decimal("4500")


async def test_precio_declarado_override_gana_al_motor():
    prod = _producto(
        precio="5000",
        precio_umbral=Decimal("10"),
        precio_bajo_umbral=Decimal("5000"),
        precio_sobre_umbral=Decimal("4500"),
    )
    repo = FakeRepo(productos={1: prod}, stock={1: Decimal("100")})
    datos = VentaCrear(
        metodo_pago="efectivo",
        lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("12"), precio_unitario=Decimal("4000"))],
    )
    res = await (VentaService(repo)).registrar_venta(datos, vendedor_id=7)
    assert res.venta.total == Decimal("48000.00")        # 4000 declarado * 12, ignora el motor
    assert repo.creado.lineas[0].precio_unitario == Decimal("4000")


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


# --- Borrado de venta (guards del servicio, repo falso) -----------------------------------------
class FakeBorradoRepo:
    def __init__(self, *, cabecera=None, factura_viva=False):
        self._cabecera = cabecera
        self._factura_viva = factura_viva
        self.borrado = None
        self.revertido = None

    async def obtener_cabecera(self, venta_id):
        return self._cabecera

    async def tiene_factura_viva(self, venta_id):
        return self._factura_viva

    async def borrar_venta(self, venta_id):
        self.borrado = venta_id

    async def revertir_lineas(self, venta_id):
        self.revertido = venta_id

    async def aplicar_edicion(self, venta_id, edicion):  # no se alcanza en los tests de guard
        raise AssertionError("aplicar_edicion no debería llamarse cuando un guard falla")


def _cabecera(*, venta_id=1, vendedor_id=7, fecha=None):
    return VentaLeer(
        id=venta_id, consecutivo=1, cliente_id=None, vendedor_id=vendedor_id,
        fecha=fecha or now_co(), subtotal=Decimal("0"), impuestos=Decimal("0"),
        total=Decimal("0"), metodo_pago="efectivo", estado="completada", origen="web",
        idempotency_key=None,
    )


async def test_borrar_de_hoy_dueno_sin_factura_delega_al_repo():
    repo = FakeBorradoRepo(cabecera=_cabecera(venta_id=3, vendedor_id=7))
    out = await VentaService(repo).borrar_venta(3, user_id=7, es_admin=False)
    assert out == 3
    assert repo.borrado == 3


async def test_borrar_inexistente_lanza_no_encontrada():
    repo = FakeBorradoRepo(cabecera=None)
    with pytest.raises(VentaNoEncontrada):
        await VentaService(repo).borrar_venta(99, user_id=7, es_admin=True)
    assert repo.borrado is None


async def test_borrar_de_dia_anterior_lanza_no_es_de_hoy():
    ayer = now_co() - timedelta(days=1)
    repo = FakeBorradoRepo(cabecera=_cabecera(fecha=ayer, vendedor_id=7))
    with pytest.raises(VentaNoEsDeHoy):
        await VentaService(repo).borrar_venta(1, user_id=7, es_admin=True)
    assert repo.borrado is None


async def test_vendedor_no_puede_borrar_la_de_otro():
    repo = FakeBorradoRepo(cabecera=_cabecera(vendedor_id=7))   # dueño = 7
    with pytest.raises(OperacionNoAutorizada):
        await VentaService(repo).borrar_venta(1, user_id=8, es_admin=False)  # lo intenta el 8
    assert repo.borrado is None


async def test_admin_borra_venta_ajena_de_hoy():
    repo = FakeBorradoRepo(cabecera=_cabecera(vendedor_id=7))
    out = await VentaService(repo).borrar_venta(1, user_id=999, es_admin=True)
    assert out == 1 and repo.borrado == 1


async def test_factura_viva_bloquea_el_borrado():
    repo = FakeBorradoRepo(cabecera=_cabecera(vendedor_id=7), factura_viva=True)
    with pytest.raises(VentaConFacturaViva):
        await VentaService(repo).borrar_venta(1, user_id=7, es_admin=False)
    assert repo.borrado is None   # nunca se borró


# --- Edición de venta: los guards (idénticos al borrado) cortan antes de tocar stock --------------
def _datos_edicion():
    return VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("1"))])


async def test_editar_inexistente_lanza_no_encontrada():
    repo = FakeBorradoRepo(cabecera=None)
    with pytest.raises(VentaNoEncontrada):
        await VentaService(repo).editar_venta(99, _datos_edicion(), user_id=7, es_admin=True)
    assert repo.revertido is None   # ni siquiera se intentó revertir


async def test_editar_de_dia_anterior_lanza_no_es_de_hoy():
    ayer = now_co() - timedelta(days=1)
    repo = FakeBorradoRepo(cabecera=_cabecera(fecha=ayer, vendedor_id=7))
    with pytest.raises(VentaNoEsDeHoy):
        await VentaService(repo).editar_venta(1, _datos_edicion(), user_id=7, es_admin=True)
    assert repo.revertido is None


async def test_vendedor_no_puede_editar_la_de_otro():
    repo = FakeBorradoRepo(cabecera=_cabecera(vendedor_id=7))
    with pytest.raises(OperacionNoAutorizada):
        await VentaService(repo).editar_venta(1, _datos_edicion(), user_id=8, es_admin=False)
    assert repo.revertido is None


async def test_factura_viva_bloquea_la_edicion():
    repo = FakeBorradoRepo(cabecera=_cabecera(vendedor_id=7), factura_viva=True)
    with pytest.raises(VentaConFacturaViva):
        await VentaService(repo).editar_venta(1, _datos_edicion(), user_id=7, es_admin=False)
    assert repo.revertido is None


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
