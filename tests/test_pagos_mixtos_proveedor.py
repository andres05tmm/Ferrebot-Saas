"""Pago MIXTO a proveedor: una parte en efectivo del cajón y otra por transferencia (0068).

Invariante: **el cajón solo se mueve por la parte que salió de él**. El resto es plata que salió del
negocio sin pasar por la caja — se registra con su medio y su monto (antes solo cabía un medio, sin
monto, así que un pago mixto era imposible de contar).

Se prueba en las tres superficies donde sale plata hacia el proveedor: al pedir, al recibir y al
abonar una cuenta por pagar; más el desglose del reporte.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import (
    LineaPedidoCrear,
    LineaRecibir,
    PedidoCrear,
    ProveedorRef,
    RecibirPedido,
)
from modules.pedidos_proveedor.service import PedidosProveedorService
from modules.proveedores.pagos import PagoInvalido, PartePago
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import AbonoCrear, FacturaProveedorCrear
from modules.proveedores.service import ProveedoresService
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.service import ReportesService


def _pedidos(s: AsyncSession) -> PedidosProveedorService:
    return PedidosProveedorService(
        SqlPedidosProveedorRepository(s),
        compras=ComprasService(SqlComprasRepository(s)),
        compras_repo=SqlComprasRepository(s),
        proveedores=SqlProveedoresRepository(s),
        caja=CajaService(SqlCajaRepository(s)),
        inventario=InventarioService(SqlInventarioRepository(s)),
    )


def _pedido(pid: int, **extra) -> PedidoCrear:
    extra.setdefault("condicion_pago", "credito")
    return PedidoCrear(
        proveedor=ProveedorRef(nombre="Ferrisariato"),
        lineas=[LineaPedidoCrear(
            producto_id=pid, cantidad=Decimal("10"), costo_estimado=Decimal("5000")
        )],
        **extra,
    )


async def _abrir_caja(engine, uid: int) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("500000"))
        await s.commit()


async def _egresos(engine) -> list[Decimal]:
    async with AsyncSession(engine) as s:
        return [
            Decimal(m) for m in (
                await s.execute(
                    text("SELECT monto FROM caja_movimientos WHERE tipo='egreso' ORDER BY id")
                )
            ).scalars().all()
        ]


async def _partes(engine, ref_tipo: str) -> list[tuple]:
    async with AsyncSession(engine) as s:
        return [
            (f.origen, Decimal(f.monto), f.caja_movimiento_id)
            for f in (
                await s.execute(
                    text(
                        "SELECT origen, monto, caja_movimiento_id FROM pagos_proveedor "
                        "WHERE ref_tipo = :t ORDER BY origen"
                    ),
                    {"t": ref_tipo},
                )
            ).all()
        ]


# --- Al pedir ----------------------------------------------------------------

async def test_pago_al_pedir_mixto_solo_egresa_la_parte_del_cajon(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _pedidos(s).crear(
            _pedido(
                pid, condicion_pago="contado",
                pagos=[
                    PartePago(origen="caja", monto=Decimal("20000")),
                    PartePago(origen="banco", monto=Decimal("30000")),
                ],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert res.pedido.anticipo == Decimal("50000.00")     # se pagó completo, en dos medios
    assert await _egresos(tenant.engine) == [Decimal("20000.00")]   # el cajón solo puso su parte

    partes = await _partes(tenant.engine, "pedido")
    assert [(o, m) for o, m, _ in partes] == [
        ("banco", Decimal("30000.00")), ("caja", Decimal("20000.00")),
    ]
    banco, caja = partes
    assert banco[2] is None and caja[2] is not None       # solo la parte de caja tiene movimiento

    async with AsyncSession(tenant.engine) as s:
        origen = (
            await s.execute(
                text("SELECT origen_anticipo FROM pedidos_proveedor WHERE id=:i"),
                {"i": res.pedido.id},
            )
        ).scalar_one()
    assert origen == "mixto"


async def test_partes_que_no_suman_el_pago_se_rechazan(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PagoInvalido):
            await _pedidos(s).crear(
                _pedido(
                    pid, condicion_pago="contado",
                    pagos=[
                        PartePago(origen="caja", monto=Decimal("20000")),
                        PartePago(origen="banco", monto=Decimal("10000")),   # suman 30k de 50k
                    ],
                ),
                usuario_id=uid,
            )
        await s.rollback()
    assert await _egresos(tenant.engine) == []


async def test_no_se_repite_el_mismo_medio(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PagoInvalido):
            await _pedidos(s).crear(
                _pedido(
                    pid, condicion_pago="contado",
                    pagos=[
                        PartePago(origen="caja", monto=Decimal("25000")),
                        PartePago(origen="caja", monto=Decimal("25000")),
                    ],
                ),
                usuario_id=uid,
            )
        await s.rollback()


# --- Al recibir ---------------------------------------------------------------

async def test_pago_al_recibir_mixto(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pedido = (await _pedidos(s).crear(_pedido(pid), usuario_id=uid)).pedido
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _pedidos(s).recibir(
            pedido.id,
            RecibirPedido(
                lineas=[LineaRecibir(
                    producto_id=pid, cantidad=Decimal("10"), costo=Decimal("5000")
                )],
                condicion_pago="contado", pago_ahora=True,
                pagos=[
                    PartePago(origen="caja", monto=Decimal("15000")),
                    PartePago(origen="efectivo_externo", monto=Decimal("35000")),
                ],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert await _egresos(tenant.engine) == [Decimal("15000.00")]
    assert [(o, m) for o, m, _ in await _partes(tenant.engine, "compra")] == [
        ("caja", Decimal("15000.00")), ("efectivo_externo", Decimal("35000.00")),
    ]


# --- Abono a una cuenta por pagar --------------------------------------------

async def test_abono_mixto_baja_la_deuda_completa_y_egresa_solo_su_parte(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(
                text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
            )
        ).scalar_one()
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        prov = ProveedoresService(SqlProveedoresRepository(s), caja=CajaService(SqlCajaRepository(s)))
        await prov.crear_factura(
            FacturaProveedorCrear(
                id="F-MX", proveedor="Ferrisariato", total=Decimal("100000"), fecha=today_co()
            ),
            usuario_id=uid,
        )
        factura = await prov.registrar_abono(
            AbonoCrear(
                factura_id="F-MX", monto=Decimal("60000"),
                pagos=[
                    PartePago(origen="caja", monto=Decimal("40000")),
                    PartePago(origen="banco", monto=Decimal("20000")),
                ],
            ),
            usuario_id=uid,
        )
        await s.commit()

    assert factura.pendiente == Decimal("40000.00")      # la deuda baja por el abono COMPLETO
    assert await _egresos(tenant.engine) == [Decimal("40000.00")]
    assert [(o, m) for o, m, _ in await _partes(tenant.engine, "abono")] == [
        ("banco", Decimal("20000.00")), ("caja", Decimal("40000.00")),
    ]


# --- Reporte ------------------------------------------------------------------

async def test_flujo_separa_lo_pagado_por_fuera_de_la_caja(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
    await _abrir_caja(tenant.engine, uid)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _pedidos(s).crear(
            _pedido(
                pid, condicion_pago="contado",
                pagos=[
                    PartePago(origen="caja", monto=Decimal("20000")),
                    PartePago(origen="banco", monto=Decimal("30000")),
                ],
            ),
            usuario_id=uid,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        flujo = await ReportesService(SqlReportesRepository(s)).flujo_dinero(desde=None, hasta=None)

    assert flujo.egresos_por_origen == {"Anticipos y pagos al pedir": Decimal("20000.00")}
    assert flujo.pagado_fuera_de_caja == Decimal("30000.00")
    assert flujo.fuera_de_caja_por_medio == {"Transferencia / banco": Decimal("30000.00")}
    # La plata que salió del negocio es la suma de las dos: el arqueo del cajón sigue viendo 20.000.
    assert flujo.total_salidas == Decimal("50000.00")
