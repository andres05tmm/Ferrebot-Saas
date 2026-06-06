"""Herramientas de SOLO LECTURA del bot (consultar_ventas_dia / consultar_producto), fase RED.

Pin del contrato de las dos consultas, todo con fakes (cero red, cero PG), al estilo de
test_dispatcher.py: un fake de `VentaService` provee los dos métodos de lectura nuevos y los
handlers se invocan por la tabla pública `POR_NOMBRE` (también verifica el cableado).

Lo que se fija aquí:
  - registro: ambas en CATALOGO/POR_NOMBRE con rol_min, feature y flags read-only correctos, y su
    `spec` expone los parámetros del args_model;
  - ventas: scope RBAC (vendedor → su id; admin → None) y resumen con conteo+total; caso sin ventas;
  - producto: único (precio+stock), ambiguo (enumera candidatos), no encontrado (ErrorTool recuperable).

RED: los handlers y los métodos de servicio son esqueletos (NotImplementedError); los tests de
comportamiento fallan a propósito hasta la fase GREEN. Los tests de registro/spec ya pasan (es la
estructura cableada en este paso).
"""
from decimal import Decimal

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.tools import (
    CATALOGO,
    POR_NOMBRE,
    ConsultarProductoArgs,
    ConsultarVentasDiaArgs,
    Deps,
)
from core.config.timezone import now_co
from modules.ventas.schemas import VentaLeer
from modules.ventas.service import FraccionBusqueda, ProductoBusqueda


# --------------------------- Fakes ----------------------------------------
class _FakeVentaService:
    """Fake de VentaService: solo los dos métodos de lectura que usan los handlers de consulta.

    Captura el argumento recibido (vendedor_id / texto) para verificar el scope RBAC y el cableado.
    """

    def __init__(self, *, ventas=None, productos=None):
        self._ventas = list(ventas or [])
        self._productos = list(productos or [])
        self.vendedor_id_recibido = "<no-llamado>"
        self.texto_recibido = "<no-llamado>"

    async def listar_dia(self, *, vendedor_id):
        self.vendedor_id_recibido = vendedor_id
        return list(self._ventas)

    async def buscar_producto_por_nombre(self, texto):
        self.texto_recibido = texto
        return list(self._productos)


# --------------------------- Helpers --------------------------------------
def _ctx(rol="vendedor", usuario_id=42) -> Contexto:
    return Contexto(tenant_id=1, usuario_id=usuario_id, rol=rol, origen="bot",
                    capacidades=frozenset({"bot_telegram"}))


def _deps(ventas) -> Deps:
    # Solo se usa deps.ventas en estas consultas; el resto no se toca.
    return Deps(ventas=ventas, caja=None, fiados=None, clientes=None)


def _venta(consecutivo, total, metodo_pago="efectivo", vendedor_id=42) -> VentaLeer:
    return VentaLeer(
        id=consecutivo, consecutivo=consecutivo, cliente_id=None, vendedor_id=vendedor_id,
        fecha=now_co(), subtotal=Decimal(total), impuestos=Decimal("0"), total=Decimal(total),
        metodo_pago=metodo_pago, estado="completada", origen="bot", idempotency_key=None,
    )


def _frac(etiqueta, precio_total) -> FraccionBusqueda:
    return FraccionBusqueda(etiqueta=etiqueta, precio_total=Decimal(precio_total))


def _prod(id_, nombre, precio="1000", stock="5", unidad="Unidad", fracciones=()) -> ProductoBusqueda:
    return ProductoBusqueda(
        id=id_, nombre=nombre, precio=Decimal(precio), stock=Decimal(stock),
        unidad_medida=unidad, fracciones=tuple(fracciones),
    )


# --------------------------- Registro / spec (estructura) -----------------
def test_consultar_ventas_dia_registrada_read_only():
    t = POR_NOMBRE["consultar_ventas_dia"]
    assert t in CATALOGO
    assert t.rol_min == "vendedor" and t.feature is None
    assert t.valida_productos is False and t.confirmable is False   # solo lectura


def test_consultar_producto_registrada_read_only():
    t = POR_NOMBRE["consultar_producto"]
    assert t in CATALOGO
    assert t.rol_min == "vendedor" and t.feature is None
    assert t.valida_productos is False and t.confirmable is False   # solo lectura


def test_specs_exponen_parametros_del_args_model():
    # consultar_producto expone "nombre"; consultar_ventas_dia no tiene parámetros.
    props_prod = POR_NOMBRE["consultar_producto"].spec.parameters["properties"]
    assert "nombre" in props_prod
    props_ventas = POR_NOMBRE["consultar_ventas_dia"].spec.parameters.get("properties", {})
    assert "nombre" not in props_ventas


# --------------------------- consultar_ventas_dia -------------------------
async def test_ventas_dia_scope_vendedor_resume_conteo_y_total():
    svc = _FakeVentaService(ventas=[_venta(1, "10000"), _venta(2, "5000")])
    res = await POR_NOMBRE["consultar_ventas_dia"].handler(
        ConsultarVentasDiaArgs(), _ctx(rol="vendedor", usuario_id=42), _deps(svc)
    )
    assert svc.vendedor_id_recibido == 42                  # acotado al vendedor (RBAC)
    assert isinstance(res, Resultado)
    assert res.evento is None and res.idempotente is None  # no muta
    assert res.data["conteo"] == 2
    assert "2" in res.resumen and "15000" in res.resumen   # conteo + total en el texto


async def test_ventas_dia_scope_admin_ve_todo():
    svc = _FakeVentaService(ventas=[_venta(1, "10000")])
    res = await POR_NOMBRE["consultar_ventas_dia"].handler(
        ConsultarVentasDiaArgs(), _ctx(rol="admin", usuario_id=99), _deps(svc)
    )
    assert svc.vendedor_id_recibido is None                # admin ve todas
    assert isinstance(res, Resultado)


async def test_ventas_dia_sin_ventas():
    svc = _FakeVentaService(ventas=[])
    res = await POR_NOMBRE["consultar_ventas_dia"].handler(
        ConsultarVentasDiaArgs(), _ctx(), _deps(svc)
    )
    assert isinstance(res, Resultado)
    assert res.data["conteo"] == 0
    assert "no hay ventas" in res.resumen.lower()


# --------------------------- consultar_producto --------------------------
async def test_producto_unico_sin_fracciones_es_simple():
    svc = _FakeVentaService(productos=[_prod(7, "Martillo", precio="12000", stock="5", unidad="Unidad")])
    res = await POR_NOMBRE["consultar_producto"].handler(
        ConsultarProductoArgs(nombre="martillo"), _ctx(), _deps(svc)
    )
    assert svc.texto_recibido == "martillo"
    assert isinstance(res, Resultado)
    assert res.data["id"] == 7 and res.data["nombre"] == "Martillo"
    assert res.data["unidad_medida"] == "Unidad" and res.data["fracciones"] == []
    assert "Unidad" in res.resumen and "12000" in res.resumen and "5" in res.resumen
    assert "Fracciones" not in res.resumen          # sin fracciones → resumen simple


async def test_producto_unico_con_fracciones_incluye_etiqueta_precio_y_unidad():
    svc = _FakeVentaService(productos=[_prod(
        3, "Thinner", precio="26000", stock="0", unidad="Galón",
        fracciones=[_frac("1/2", "13000"), _frac("1/4", "7000")],
    )])
    res = await POR_NOMBRE["consultar_producto"].handler(
        ConsultarProductoArgs(nombre="thinner"), _ctx(), _deps(svc)
    )
    assert isinstance(res, Resultado)
    # unidad + ambas fracciones (etiqueta y precio) + precio base + stock, todo en el resumen
    for fragmento in ("Thinner", "Galón", "26000", "1/2", "13000", "1/4", "7000"):
        assert fragmento in res.resumen
    assert "Stock: 0" in res.resumen
    assert res.data["unidad_medida"] == "Galón"
    assert res.data["fracciones"][0] == {"etiqueta": "1/2", "precio_total": "13000"}
    assert len(res.data["fracciones"]) == 2


async def test_producto_ambiguo_enumera_candidatos():
    svc = _FakeVentaService(productos=[
        _prod(1, "Martillo carpintero"), _prod(2, "Martillo de bola"),
    ])
    res = await POR_NOMBRE["consultar_producto"].handler(
        ConsultarProductoArgs(nombre="martillo"), _ctx(), _deps(svc)
    )
    assert isinstance(res, Resultado)
    assert "Martillo carpintero" in res.resumen and "Martillo de bola" in res.resumen


async def test_producto_no_encontrado_es_error_recuperable():
    svc = _FakeVentaService(productos=[])
    res = await POR_NOMBRE["consultar_producto"].handler(
        ConsultarProductoArgs(nombre="xyz"), _ctx(), _deps(svc)
    )
    assert isinstance(res, ErrorTool)
    assert res.error == "producto_no_encontrado" and res.recuperable is True
