"""Modo empresa-cajón (`caja_obligatoria`): UNA caja compartida por empresa — invariantes (TDD-primero).

Cuando el toggle está ON, papá y la empleada comparten el mismo cajón físico: abrir con una caja ya
abierta (de CUALQUIER usuario) es replay; el arqueo suma las ventas en efectivo de TODOS los
vendedores en la ventana de la caja; gastos y movimientos van a LA caja abierta aunque la haya
abierto otra persona. Con el modo OFF, la semántica por-usuario existente no cambia
(test_caja.py / test_caja_fiados_paridad.py siguen siendo la paridad).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.compras.models  # noqa: F401  (registra `proveedores`: FK de gastos)
import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de gastos)
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _svc(s):
    return CajaService(SqlCajaRepository(s))


async def _otro_usuario(s: AsyncSession, nombre: str = "Empleada") -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n,'vendedor') RETURNING id"), {"n": nombre}
        )
    ).scalar_one()


async def _vender_efectivo(s: AsyncSession, *, pid: int, vendedor_id: int, cantidad: str) -> None:
    await VentaService(SqlVentasRepository(s)).registrar_venta(
        VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cantidad))]),
        vendedor_id=vendedor_id,
    )


async def test_abrir_modo_empresa_con_caja_de_otro_es_replay(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid_a, _pid = await seed_producto(s)
        uid_b = await _otro_usuario(s)
        await s.commit()
        r1 = await _svc(s).abrir(usuario_id=uid_a, saldo_inicial=Decimal("50000"))
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).abrir(usuario_id=uid_b, saldo_inicial=Decimal("999"), modo_empresa=True)
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r2.caja.id == r1.caja.id            # un solo cajón: no se abre una segunda caja
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM caja"))).scalar_one() == 1


async def test_arqueo_modo_empresa_suma_ventas_de_todos_los_vendedores(tenant, seed_producto):
    """esperado = apertura + efectivo de TODOS (papá + empleada), no solo del dueño de la caja."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid_a, pid = await seed_producto(s, precio="10000", stock="100")
        uid_b = await _otro_usuario(s)
        await s.commit()
        await _svc(s).abrir(usuario_id=uid_a, saldo_inicial=Decimal("50000"))
        await _vender_efectivo(s, pid=pid, vendedor_id=uid_a, cantidad="2")   # 20000
        await _vender_efectivo(s, pid=pid, vendedor_id=uid_b, cantidad="1")   # 10000
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        a = await _svc(s).arqueo(uid_b, modo_empresa=True)   # la consulta el OTRO usuario

    assert a is not None
    assert a.ventas_efectivo == Decimal("30000.00")
    assert a.saldo_esperado == Decimal("80000.00")

    # Paridad por-usuario intacta: sin modo_empresa, el dueño de la caja solo ve SUS ventas.
    async with AsyncSession(tenant.engine) as s:
        solo_a = await _svc(s).arqueo(uid_a)
    assert solo_a is not None and solo_a.ventas_efectivo == Decimal("20000.00")


async def test_gasto_modo_empresa_usa_la_caja_abierta_de_otro(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid_a, _pid = await seed_producto(s)
        uid_b = await _otro_usuario(s)
        await s.commit()
        caja_id = (await _svc(s).abrir(usuario_id=uid_a, saldo_inicial=Decimal("50000"))).caja.id
        await s.commit()

        # Sin modo empresa el usuario B no tiene caja → invariante por-usuario intacto.
        with pytest.raises(CajaNoAbierta):
            await _svc(s).registrar_gasto(
                usuario_id=uid_b, categoria="otros", monto=Decimal("5000"), concepto="bolsas"
            )
        await s.rollback()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).registrar_gasto(
            usuario_id=uid_b, categoria="otros", monto=Decimal("5000"), concepto="bolsas",
            modo_empresa=True,
        )
        await s.commit()

    assert res.gasto.caja_id == caja_id
    async with AsyncSession(tenant.engine) as s:
        egresos = (
            await s.execute(
                text("SELECT COALESCE(SUM(monto),0) FROM caja_movimientos WHERE caja_id=:c AND tipo='egreso'"),
                {"c": caja_id},
            )
        ).scalar_one()
    assert Decimal(egresos) == Decimal("5000.00")   # el gasto posteó SU egreso en LA caja de la empresa


async def test_cerrar_modo_empresa_cierra_la_caja_de_otro_con_arqueo_global(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid_a, pid = await seed_producto(s, precio="10000", stock="100")
        uid_b = await _otro_usuario(s)
        await s.commit()
        await _svc(s).abrir(usuario_id=uid_a, saldo_inicial=Decimal("50000"))
        await _vender_efectivo(s, pid=pid, vendedor_id=uid_b, cantidad="1")   # 10000 de B
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        caja = await _svc(s).cerrar(
            usuario_id=uid_b, saldo_contado=Decimal("60000"), modo_empresa=True
        )
        await s.commit()

    assert caja.estado == "cerrada"
    assert caja.saldo_esperado == Decimal("60000.00")   # 50000 + 10000 (venta de B cuenta)
    assert caja.diferencia == Decimal("0.00")
