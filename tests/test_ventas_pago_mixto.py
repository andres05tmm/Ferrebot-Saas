"""Pago mixto (F5, migración 0053): invariantes de dinero — TDD-primero (carve-out).

Una venta MIXTA se cobra en varios métodos a la vez (efectivo + transferencia, p. ej.) y persiste
sus partes en `ventas_pagos`. Invariantes cubiertos aquí:

  1. La suma de las partes == total de la venta (LineaInvalida → 422 en el router si no cuadra).
  2. El arqueo de caja suma SOLO la porción efectivo de una mixta (el cajón físico recibe esa parte).
  3. Idempotencia: el replay de una mixta no duplica ni la venta ni sus filas en `ventas_pagos`;
     la misma key con partes distintas es IdempotenciaConflicto.

Las ventas normales siguen igual (cero filas en `ventas_pagos`); `fiado` NO participa del mixto (v1).
"""
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.compras.models  # noqa: F401  (registra `proveedores`: FK de gastos)
import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de gastos)
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.ventas.errors import IdempotenciaConflicto, LineaInvalida
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import PagoParte, VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _mixta(producto_id, cantidad, pagos, key=None):
    return VentaCrear(
        metodo_pago="mixto",
        idempotency_key=key,
        pagos=pagos,
        lineas=[VentaDetalleCrear(producto_id=producto_id, cantidad=Decimal(cantidad))],
    )


def _pagos(*partes: tuple[str, str]) -> list[PagoParte]:
    return [PagoParte(metodo=m, monto=Decimal(v)) for m, v in partes]


# --- 1. Estructura del payload (Pydantic → 422 en el API) ---------------------------------------

def test_mixto_requiere_al_menos_dos_partes():
    with pytest.raises(ValidationError):
        _mixta(1, "1", _pagos(("efectivo", "10000")))


def test_pagos_solo_con_metodo_mixto():
    with pytest.raises(ValidationError):
        VentaCrear(
            metodo_pago="efectivo",
            pagos=_pagos(("efectivo", "5000"), ("transferencia", "5000")),
            lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("1"))],
        )


def test_fiado_no_participa_del_mixto():
    with pytest.raises(ValidationError):
        _mixta(1, "1", _pagos(("efectivo", "5000"), ("fiado", "5000")))


def test_mixto_sin_pagos_es_invalido():
    with pytest.raises(ValidationError):
        VentaCrear(
            metodo_pago="mixto",
            lineas=[VentaDetalleCrear(producto_id=1, cantidad=Decimal("1"))],
        )


# --- 2. Suma de las partes == total (invariante de dinero, lo valida el servicio) ---------------

async def test_suma_de_pagos_distinta_del_total_no_registra_nada(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        # total real = 20000; las partes suman 19000 → rechazo sin efectos.
        with pytest.raises(LineaInvalida):
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                _mixta(pid, "2", _pagos(("efectivo", "10000"), ("transferencia", "9000"))), uid
            )
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM ventas_pagos"))).scalar_one() == 0
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("100.000")   # intacto: nada movió stock


async def test_mixta_persiste_sus_partes_y_la_normal_ninguna(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        res = await VentaService(SqlVentasRepository(s)).registrar_venta(
            _mixta(pid, "2", _pagos(("efectivo", "12000"), ("transferencia", "8000"))), uid
        )
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))],
            ),
            uid,
        )
        await s.commit()

    assert res.venta.metodo_pago == "mixto"
    async with AsyncSession(tenant.engine) as s:
        filas = (
            await s.execute(
                text("SELECT metodo, monto FROM ventas_pagos WHERE venta_id=:v ORDER BY metodo"),
                {"v": res.venta.id},
            )
        ).all()
        assert [(f.metodo, Decimal(f.monto)) for f in filas] == [
            ("efectivo", Decimal("12000.00")), ("transferencia", Decimal("8000.00")),
        ]
        # La venta normal no escribió NINGUNA fila (las no-mixtas siguen exactamente igual).
        assert (await s.execute(text("SELECT count(*) FROM ventas_pagos"))).scalar_one() == 2


# --- 3. Arqueo: el cajón solo recibe la porción efectivo ------------------------------------------

async def test_arqueo_cuenta_solo_la_porcion_efectivo_de_una_mixta(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="12500", stock="100")
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        # total 25000: 10000 en efectivo + 15000 por transferencia.
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            _mixta(pid, "2", _pagos(("efectivo", "10000"), ("transferencia", "15000"))), uid
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        a = await CajaService(SqlCajaRepository(s)).arqueo(uid)

    assert a is not None
    assert a.ventas_efectivo == Decimal("10000.00")            # SOLO la porción efectivo
    assert a.saldo_esperado == Decimal("60000.00")             # apertura + esa porción


# --- 4. Idempotencia: replay de mixta no duplica nada ---------------------------------------------

async def test_idempotencia_mixta_no_duplica_venta_ni_pagos(tenant, seed_producto):
    pagos = (("efectivo", "10000"), ("transferencia", "10000"))
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        r1 = await VentaService(SqlVentasRepository(s)).registrar_venta(
            _mixta(pid, "2", _pagos(*pagos), key="mix-dup"), uid
        )
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await VentaService(SqlVentasRepository(s)).registrar_venta(
            _mixta(pid, "2", _pagos(*pagos), key="mix-dup"), uid
        )
        await s.commit()

    assert r2.replay is True
    assert r2.venta.id == r1.venta.id
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM ventas_pagos"))).scalar_one() == 2
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("98.000")   # descontado UNA sola vez


# --- 5. Reportes: nada aparece como "mixto" en los desgloses de dinero ---------------------------

async def test_resumen_y_flujo_expanden_la_mixta_en_sus_partes(tenant, seed_producto):
    """El desglose por método reemplaza 'mixto' por sus partes reales; el total y el conteo no
    cambian (la mixta sigue siendo UNA venta)."""
    from datetime import timedelta

    from core.config.timezone import now_co
    from modules.reportes.repository import SqlReportesRepository

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        svc = VentaService(SqlVentasRepository(s))
        # Normal en efectivo (10000) + mixta 20000 = 12000 efectivo + 8000 transferencia.
        await svc.registrar_venta(
            VentaCrear(metodo_pago="efectivo",
                       lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))]), uid,
        )
        await svc.registrar_venta(
            _mixta(pid, "2", _pagos(("efectivo", "12000"), ("transferencia", "8000"))), uid
        )
        await s.commit()

    inicio, fin = now_co() - timedelta(hours=1), now_co() + timedelta(hours=1)
    async with AsyncSession(tenant.engine) as s:
        repo = SqlReportesRepository(s)
        dia = await repo.resumen(inicio=inicio, fin=fin, vendedor_id=None)
        flujo = await repo.flujo_dinero(inicio=inicio, fin=fin)

    assert "mixto" not in dia.por_metodo_pago
    assert dia.por_metodo_pago == {
        "efectivo": Decimal("22000.00"), "transferencia": Decimal("8000.00"),
    }
    assert dia.num_ventas == 2
    assert dia.total_vendido == Decimal("30000.00")

    assert "mixto" not in flujo.ventas_por_metodo
    assert flujo.ventas_por_metodo == {
        "efectivo": Decimal("22000.00"), "transferencia": Decimal("8000.00"),
    }


async def test_idempotencia_mixta_con_partes_distintas_es_conflicto(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            _mixta(pid, "2", _pagos(("efectivo", "10000"), ("transferencia", "10000")), key="mix-k"), uid
        )
        await s.commit()

    # Misma key, mismas líneas, pero el desglose de pago cambió → conflicto (409 en el router).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(IdempotenciaConflicto):
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                _mixta(pid, "2", _pagos(("efectivo", "5000"), ("transferencia", "15000")), key="mix-k"), uid
            )
        await s.rollback()
