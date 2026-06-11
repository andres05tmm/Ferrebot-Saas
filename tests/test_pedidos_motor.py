"""Motor de pedidos (ADR 0016) — determinista sobre la base efímera real.

Cubre: resolución contra el catálogo (exacta y sugerencias), stock insuficiente bloquea (sin tocar
inventario), horario de cocina, borrador único por teléfono (rearmar reemplaza), mínimo de pedido,
tarifa por zona vs default, idempotencia por key y las transiciones del ciclo.
"""
from datetime import datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ, today_co
from modules.pedidos.errors import (
    CocinaCerrada,
    PedidoMuyChico,
    ProductoNoEncontrado,
    StockInsuficiente,
    TransicionInvalida,
)
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.schemas import ZonaCrear
from modules.pedidos.service import ItemPedido, PedidosService

TEL = "3001112233"


def _ahora(hora: int = 12) -> datetime:
    return datetime.combine(today_co(), time(hora, 0), tzinfo=COLOMBIA_TZ)


async def _seed_producto(s: AsyncSession, *, nombre: str, precio: str, stock: str) -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                "permite_fraccion, activo) VALUES (:n, 'unidad', :p, 19, false, true) RETURNING id"
            ),
            {"n": nombre, "p": precio},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:pid, :s, 0)"),
        {"pid": pid, "s": stock},
    )
    await s.commit()
    return pid


async def _svc(s: AsyncSession) -> PedidosService:
    return PedidosService(SqlPedidosRepository(s))


# --- armar -------------------------------------------------------------------
async def test_armar_resuelve_catalogo_y_calcula(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        await _seed_producto(s, nombre="Coca-Cola", precio="5000", stock="20")
        svc = await _svc(s)
        res = await svc.armar_pedido(
            TEL,
            [ItemPedido("hamburguesa", Decimal("2")), ItemPedido("Coca-Cola", Decimal("1"))],
            ahora=_ahora(),
        )
        await s.commit()

    pedido = res.pedido
    assert pedido.estado == "recibido" and not res.replay
    assert pedido.subtotal == Decimal("41000.00")           # 2×18000 + 5000
    assert {i.nombre for i in pedido.items} == {"Hamburguesa", "Coca-Cola"}


async def test_armar_de_nuevo_reemplaza_el_borrador(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        svc = await _svc(s)
        primero = await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("3"))], ahora=_ahora())
        segundo = await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        await s.commit()

    assert segundo.pedido.id == primero.pedido.id           # mismo borrador, no uno nuevo
    assert segundo.pedido.subtotal == Decimal("18000.00")
    assert len(segundo.pedido.items) == 1


async def test_armar_idempotente_por_key(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        svc = await _svc(s)
        r1 = await svc.armar_pedido(
            TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora(), idempotency_key="k-1"
        )
        await s.commit()
        r2 = await svc.armar_pedido(
            TEL, [ItemPedido("Hamburguesa", Decimal("9"))], ahora=_ahora(), idempotency_key="k-1"
        )

    assert r2.replay and r2.pedido.id == r1.pedido.id
    assert r2.pedido.subtotal == Decimal("18000.00")        # el replay NO re-arma


async def test_armar_valida_catalogo_stock_y_horario(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="2")
        svc = await _svc(s)

        with pytest.raises(ProductoNoEncontrado) as exc:
            await svc.armar_pedido(TEL, [ItemPedido("pizza hawaiana", Decimal("1"))], ahora=_ahora())
        assert exc.value.nombre == "pizza hawaiana"

        with pytest.raises(StockInsuficiente):
            await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("5"))], ahora=_ahora())

        with pytest.raises(CocinaCerrada):                  # default 08:00–21:00
            await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora(23))

        # El stock NO se tocó (el pedido no descuenta inventario — regla #7).
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario LIMIT 1"))
        ).scalar_one()
        assert stock == Decimal("2.000")


# --- confirmar -----------------------------------------------------------------
async def test_confirmar_aplica_zona_o_default_y_minimo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        repo = SqlPedidosRepository(s)
        svc = PedidosService(repo)
        config = await repo.obtener_config()
        config.minimo_pedido = Decimal("10000")
        config.costo_domicilio_default = Decimal("3000")
        await repo.crear_zona(ZonaCrear(nombre="Bocagrande", tarifa=Decimal("8000")))
        await s.commit()

        await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        pedido, estimado = await svc.confirmar_pedido(
            TEL, direccion="Cra 1 # 2-3", barrio="bocagrande", metodo_pago="efectivo", nombre="Ana"
        )
        await s.commit()
        assert pedido.estado == "confirmado" and estimado == 45
        assert pedido.costo_domicilio == Decimal("8000.00")
        assert pedido.total == Decimal("26000.00")

        # Otro cliente, barrio sin zona → tarifa default; y pedido chico → bloquea.
        await svc.armar_pedido("3009990000", [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        pedido2, _ = await svc.confirmar_pedido(
            "3009990000", direccion="Cl 9 # 9-9", barrio="Manga", metodo_pago="efectivo"
        )
        assert pedido2.costo_domicilio == Decimal("3000.00")

        config.minimo_pedido = Decimal("50000")
        await s.commit()
        await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        with pytest.raises(PedidoMuyChico):
            await svc.confirmar_pedido(TEL, direccion="Cra 1 # 2-3", metodo_pago="efectivo")


# --- ciclo / kanban ---------------------------------------------------------------
async def test_transiciones_validas_e_invalidas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        svc = await _svc(s)
        await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        pedido, _ = await svc.confirmar_pedido(TEL, direccion="Cra 1 # 2-3", metodo_pago="efectivo")
        await s.commit()

        for nuevo in ("en_preparacion", "en_camino", "entregado"):
            pedido = await svc.cambiar_estado(pedido.id, nuevo)
        await s.commit()
        assert pedido.estado == "entregado"

        with pytest.raises(TransicionInvalida):             # entregado es final
            await svc.cambiar_estado(pedido.id, "cancelado")


async def test_estado_de_es_solo_del_telefono(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_producto(s, nombre="Hamburguesa", precio="18000", stock="10")
        svc = await _svc(s)
        await svc.armar_pedido(TEL, [ItemPedido("Hamburguesa", Decimal("1"))], ahora=_ahora())
        await s.commit()

        assert (await svc.estado_de(TEL)).cliente_telefono == TEL
        assert await svc.estado_de("3110000000") is None    # otro teléfono no ve nada
