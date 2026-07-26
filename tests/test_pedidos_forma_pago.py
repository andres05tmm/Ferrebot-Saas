"""Forma de pago declarada AL PEDIR — invariantes de dinero (TDD-primero).

Reforma del tab Compras: el pedido nace completo (líneas con producto, cantidad y costo unitario) y
con su forma de pago. Lo que se prueba aquí es la plata:

- contado al pedir con origen `caja` → UN egreso (key natural `pedido-anticipo:{id}`, referencia
  `pedido:{id}`) y el stock NO se mueve (la mercancía todavía no llegó);
- reintento del alta (doble clic) → replay, sigue habiendo UN solo egreso;
- caja cerrada con origen `caja` → falla y el pedido NO queda creado (sin efectos parciales);
- `efectivo_externo` / `banco` → el pago queda registrado con su origen pero NO toca la caja del día
  (es plata que no salió del cajón: el arqueo no puede descuadrarse);
- crédito → cero movimientos de caja al pedir.

Integración contra base efímera real (fixture `tenant`).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.errors import PedidoInvalido
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import LineaPedidoCrear, PedidoCrear, ProveedorRef
from modules.pedidos_proveedor.service import PedidosProveedorService
from modules.proveedores.repository import SqlProveedoresRepository


def _svc(s: AsyncSession) -> PedidosProveedorService:
    return PedidosProveedorService(
        SqlPedidosProveedorRepository(s),
        compras=ComprasService(SqlComprasRepository(s)),
        compras_repo=SqlComprasRepository(s),
        proveedores=SqlProveedoresRepository(s),
        caja=CajaService(SqlCajaRepository(s)),
        inventario=InventarioService(SqlInventarioRepository(s)),
    )


def _pedido(pid: int, *, cantidad="10", costo="5000", **extra) -> PedidoCrear:
    """Pedido con la captura completa que exige el dueño: producto + cantidad + costo unitario."""
    extra.setdefault("condicion_pago", "credito")
    return PedidoCrear(
        proveedor=ProveedorRef(nombre="Ferrisariato"),
        descripcion="Pedido del lunes",
        lineas=[
            LineaPedidoCrear(
                producto_id=pid, cantidad=Decimal(cantidad), costo_estimado=Decimal(costo)
            )
        ],
        **extra,
    )


async def _caja_abierta(engine, usuario_id: int) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await CajaService(SqlCajaRepository(s)).abrir(
            usuario_id=usuario_id, saldo_inicial=Decimal("200000")
        )
        await s.commit()


async def _egresos(engine) -> list[tuple]:
    async with AsyncSession(engine) as s:
        filas = (
            await s.execute(
                text(
                    "SELECT monto, referencia, idempotency_key FROM caja_movimientos "
                    "WHERE tipo='egreso' ORDER BY id"
                )
            )
        ).all()
    return [tuple(f) for f in filas]


async def _stock(engine, producto_id: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": producto_id}
            )
        ).scalar_one()


# --- Contado al pedir --------------------------------------------------------

async def test_contado_al_pedir_egresa_una_vez_y_no_mueve_stock(tenant, seed_producto):
    """El pago sale completo al hacer el pedido; la mercancía todavía no llegó → stock intacto."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
    await _caja_abierta(tenant.engine, uid)

    datos = _pedido(pid, condicion_pago="contado", idempotency_key="ped-contado-1")
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).crear(datos, usuario_id=uid)
        await s.commit()

    assert r1.replay is False
    assert r1.pedido.condicion_pago == "contado"
    assert r1.pedido.anticipo == Decimal("50000.00")     # contado = el valor completo del pedido

    egresos = await _egresos(tenant.engine)
    assert len(egresos) == 1
    monto, referencia, key = egresos[0]
    assert Decimal(monto) == Decimal("50000.00")
    assert referencia == f"pedido:{r1.pedido.id}"        # de dónde viene el egreso
    assert key == f"pedido-anticipo:{r1.pedido.id}"      # key natural anti doble-egreso
    assert await _stock(tenant.engine, pid) == Decimal("0.000")

    # Doble clic del alta: replay, sin segundo egreso.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).crear(datos, usuario_id=uid)
        await s.commit()
    assert r2.replay is True and r2.pedido.id == r1.pedido.id
    assert len(await _egresos(tenant.engine)) == 1


async def test_contado_sin_caja_abierta_no_deja_pedido(tenant, seed_producto):
    """Sin caja no hay de dónde sacar la plata: falla y el pedido NO se crea (sin efectos parciales)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaNoAbierta):
            await _svc(s).crear(_pedido(pid, condicion_pago="contado"), usuario_id=uid)
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        pedidos = (await s.execute(text("SELECT count(*) FROM pedidos_proveedor"))).scalar_one()
    assert pedidos == 0
    assert await _egresos(tenant.engine) == []


# --- De dónde sale la plata --------------------------------------------------

async def test_pago_por_fuera_de_la_caja_se_registra_pero_no_descuadra_el_arqueo(
    tenant, seed_producto
):
    """Efectivo guardado de días anteriores (o transferencia): la compra queda pagada y con su
    origen, pero la caja del día NO se toca."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
    await _caja_abierta(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).crear(
            _pedido(pid, condicion_pago="contado", origen_fondos="efectivo_externo"),
            usuario_id=uid,
        )
        await s.commit()

    assert res.pedido.anticipo == Decimal("50000.00")
    assert await _egresos(tenant.engine) == []          # el cajón del día no se movió
    async with AsyncSession(tenant.engine) as s:
        origen = (
            await s.execute(
                text("SELECT origen_anticipo FROM pedidos_proveedor WHERE id=:i"),
                {"i": res.pedido.id},
            )
        ).scalar_one()
    assert origen == "efectivo_externo"                 # queda la procedencia del dinero


async def test_credito_no_mueve_caja_al_pedir(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
    await _caja_abierta(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).crear(_pedido(pid, condicion_pago="credito"), usuario_id=uid)
        await s.commit()

    assert res.pedido.anticipo is None
    assert await _egresos(tenant.engine) == []


# --- Reglas de la forma de pago ---------------------------------------------

async def test_anticipo_parcial_debe_ser_menor_que_el_total(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
    await _caja_abierta(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PedidoInvalido):
            await _svc(s).crear(
                _pedido(pid, condicion_pago="anticipado", anticipo=Decimal("50000")),
                usuario_id=uid,
            )
        await s.rollback()


async def test_credito_con_anticipo_es_invalido(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PedidoInvalido):
            await _svc(s).crear(
                _pedido(pid, condicion_pago="credito", anticipo=Decimal("10000")), usuario_id=uid
            )
        await s.rollback()


async def test_producto_inexistente_es_rechazado_antes_de_tocar_la_base(tenant, seed_producto):
    """Antes reventaba contra la FK (500). Ahora se valida y se rechaza con un error de dominio."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await seed_producto(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PedidoInvalido):
            await _svc(s).crear(_pedido(999_999), usuario_id=uid)
        await s.rollback()


def test_pedido_sin_lineas_no_valida():
    """Captura obligatoria (decisión del dueño): sin productos no hay pedido."""
    with pytest.raises(ValueError):
        PedidoCrear(
            proveedor=ProveedorRef(nombre="Ferrisariato"),
            descripcion="lo de siempre",
            condicion_pago="credito",
        )


def test_linea_sin_costo_unitario_no_valida():
    with pytest.raises(ValueError):
        LineaPedidoCrear(producto_id=1, cantidad=Decimal("5"))
