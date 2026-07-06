"""Integración de caja/gastos contra base efímera: apertura idempotente, lock e idempotencia."""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id"))
    ).scalar_one()


def _svc(s):
    return CajaService(SqlCajaRepository(s))


async def test_apertura_idempotente_una_sola_caja(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        r1 = await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("999999"))
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r2.caja.id == r1.caja.id
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM caja"))).scalar_one() == 1


async def test_movimiento_sin_caja_abierta_falla(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        with pytest.raises(CajaNoAbierta):
            await _svc(s).registrar_movimiento(
                usuario_id=uid, tipo="ingreso", monto=Decimal("1000"), concepto="x"
            )
        await s.rollback()


async def test_movimiento_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        r1 = await _svc(s).registrar_movimiento(
            usuario_id=uid, tipo="ingreso", monto=Decimal("5000"), concepto="ajuste", idempotency_key="m1"
        )
        await s.commit()
        mid = r1.movimiento.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).registrar_movimiento(
            usuario_id=uid, tipo="ingreso", monto=Decimal("5000"), concepto="ajuste", idempotency_key="m1"
        )
        await s.commit()

    assert r2.replay is True
    assert r2.movimiento.id == mid
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM caja_movimientos"))).scalar_one() == 1


async def test_arqueo_sin_caja_es_none(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        assert await _svc(s).arqueo(uid) is None       # sin caja abierta → None (el router mapea a 'cerrada')


async def test_arqueo_en_vivo_calcula_esperado(tenant):
    """El arqueo en vivo reusa la fórmula del cierre: esperado = apertura + ventas_efectivo + ingresos − egresos.
    Con una venta en efectivo, un ingreso y un egreso manuales, cuadra el esperado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = (
            await s.execute(text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',20000,12000,12000,0,false,true) RETURNING id"
            ))
        ).scalar_one()
        await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"), {"p": pid})
        await s.commit()
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        await s.commit()
        # Venta en efectivo del mismo usuario (entra al arqueo de su caja).
        from modules.ventas.repository import SqlVentasRepository
        from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
        from modules.ventas.service import VentaService
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))]),
            vendedor_id=uid,
        )
        await _svc(s).registrar_movimiento(usuario_id=uid, tipo="ingreso", monto=Decimal("3000"), concepto="ajuste")
        await _svc(s).registrar_movimiento(usuario_id=uid, tipo="egreso", monto=Decimal("2000"), concepto="propina")
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        a = await _svc(s).arqueo(uid)

    assert a is not None
    assert a.ventas_efectivo == Decimal("20000.00")     # 1 × 20000
    assert a.ingresos == Decimal("3000.00")
    assert a.egresos == Decimal("2000.00")
    # esperado = 50000 + 20000 + 3000 − 2000 = 71000
    assert a.saldo_esperado == Decimal("71000.00")


# --- HTTP: GET /caja/arqueo (mapeo cerrada/abierta) ---------------------------

async def test_http_arqueo_cerrada_y_abierta(tenant):
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user, require_role
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.caja.router import router as caja_router

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()

    app = FastAPI()
    app.include_router(caja_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            yield s
            await s.commit()

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=uid, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"caja"})
    app.dependency_overrides[get_tenant_db] = _db

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        cerrada = await c.get("/api/v1/caja/arqueo")             # sin caja → 200 'cerrada'
        await c.post("/api/v1/caja/apertura", json={"saldo_inicial": "40000"})
        abierta = await c.get("/api/v1/caja/arqueo")

    assert cerrada.status_code == 200 and cerrada.json()["estado"] == "cerrada"
    body = abierta.json()
    assert body["estado"] == "abierta"
    assert body["saldo_inicial"] == "40000.00" and body["saldo_esperado"] == "40000.00"
