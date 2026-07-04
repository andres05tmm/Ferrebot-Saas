"""Retenciones (ADR 0027) contra Postgres efímero: catálogo, aplicación a documentos e invariantes.

Invariantes críticos (TDD): (1) con retenciones activas el total de la venta NO cambia y la caja cuadra
(neto = total − retenido); (2) SIN config nada cambia (regresión); (3) reaplicar es idempotente (no
duplica renglones); (4) aislamiento multi-tenant del catálogo y de los renglones.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.service import RetencionesService
from modules.retenciones.schemas import ReglaUpsert


async def _venta(s: AsyncSession, *, consecutivo=1, subtotal="1000000.00", impuestos="190000.00",
                 total="1190000.00", estado="completada") -> int:
    uid = (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Ana','vendedor') RETURNING id"))
    ).scalar_one()
    return (
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                "metodo_pago, estado, origen) "
                "VALUES (:c,:v,:f,:s,:i,:t,'efectivo',:e,'web') RETURNING id"
            ),
            {"c": consecutivo, "v": uid, "f": now_co(), "s": subtotal, "i": impuestos, "t": total, "e": estado},
        )
    ).scalar_one()


async def _seed_reglas(s: AsyncSession) -> None:
    svc = RetencionesService(SqlRetencionesRepository(s))
    await svc.upsert_regla(ReglaUpsert(tipo="uvt", concepto="2026", tarifa=Decimal("49799")))
    await svc.upsert_regla(ReglaUpsert(tipo="retefuente", concepto="compras", tarifa=Decimal("2.5")))
    await svc.upsert_regla(ReglaUpsert(tipo="reteiva", concepto="reteiva", tarifa=Decimal("15")))


# ── Catálogo ────────────────────────────────────────────────────────────────
async def test_upsert_es_idempotente_por_tipo_concepto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = RetencionesService(SqlRetencionesRepository(s))
        await svc.upsert_regla(ReglaUpsert(tipo="retefuente", concepto="servicios", tarifa=Decimal("4")))
        await svc.upsert_regla(ReglaUpsert(tipo="retefuente", concepto="servicios", tarifa=Decimal("6")))
        reglas = await svc.listar_config()
    servicios = [r for r in reglas if r.concepto == "servicios"]
    assert len(servicios) == 1               # misma clave natural → una sola fila
    assert servicios[0].tarifa == Decimal("6.0000")   # el segundo upsert gana


# ── Invariante: totales cuadran (retención = menor pago, no menor venta) ─────
async def test_con_retenciones_total_venta_intacto_y_caja_cuadra(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        venta_id = await _venta(s)
        await s.commit()
        await _seed_reglas(s)
        resumen = await RetencionesService(SqlRetencionesRepository(s)).aplicar_a_venta(venta_id)

    # retefuente 2.5% de 1.000.000 = 25.000 ; reteIVA 15% de 190.000 = 28.500 ; retenido = 53.500
    assert resumen is not None
    assert resumen.total_documento == Decimal("1190000.00")   # la venta NO cambia
    assert resumen.total_retenido == Decimal("53500.00")
    assert resumen.neto_a_recibir == Decimal("1136500.00")    # 1.190.000 − 53.500 (caja cuadra)

    async with AsyncSession(tenant.engine) as s:
        # El total de la venta quedó intacto en la tabla (nada mutó la venta).
        total = (await s.execute(text("SELECT total FROM ventas WHERE id=:i"), {"i": venta_id})).scalar_one()
        assert total == Decimal("1190000.00")
        n = (await s.execute(text("SELECT count(*) FROM retenciones_documento WHERE doc_id=:i"), {"i": venta_id})).scalar_one()
        assert n == 2   # retefuente + reteiva persistidos aparte


async def test_sin_config_no_cambia_nada(tenant):
    # Regresión: sin reglas, aplicar no crea renglones y el neto = total.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        venta_id = await _venta(s)
        await s.commit()
        resumen = await RetencionesService(SqlRetencionesRepository(s)).aplicar_a_venta(venta_id)
        n = (await s.execute(text("SELECT count(*) FROM retenciones_documento"))).scalar_one()

    assert resumen is not None
    assert resumen.retenciones == []
    assert resumen.total_retenido == Decimal("0.00")
    assert resumen.neto_a_recibir == resumen.total_documento == Decimal("1190000.00")
    assert n == 0


async def test_reaplicar_es_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        venta_id = await _venta(s)
        await s.commit()
        await _seed_reglas(s)
        svc = RetencionesService(SqlRetencionesRepository(s))
        await svc.aplicar_a_venta(venta_id)
        await svc.aplicar_a_venta(venta_id)   # reproceso
        n = (await s.execute(text("SELECT count(*) FROM retenciones_documento WHERE doc_id=:i"), {"i": venta_id})).scalar_one()
        suma = (await s.execute(text("SELECT coalesce(sum(valor),0) FROM retenciones_documento WHERE doc_id=:i"), {"i": venta_id})).scalar_one()
    assert n == 2                       # NO se duplicó
    assert Decimal(suma) == Decimal("53500.00")


async def test_venta_inexistente_o_anulada_da_none(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        anulada = await _venta(s, consecutivo=9, estado="anulada")
        await s.commit()
        svc = RetencionesService(SqlRetencionesRepository(s))
        assert await svc.aplicar_a_venta(9999) is None       # no existe
        assert await svc.aplicar_a_venta(anulada) is None    # anulada no retiene


# ── Aislamiento multi-tenant ─────────────────────────────────────────────────
async def test_aislamiento_config_y_renglones_entre_tenants(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()

    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _seed_reglas(s)
        venta_a = await _venta(s, consecutivo=1)
        await s.commit()
        await RetencionesService(SqlRetencionesRepository(s)).aplicar_a_venta(venta_a)

    # B no ve el catálogo ni los renglones de A.
    async with AsyncSession(b.engine) as s:
        reglas_b = await RetencionesService(SqlRetencionesRepository(s)).listar_config()
        n_b = (await s.execute(text("SELECT count(*) FROM retenciones_documento"))).scalar_one()
    assert reglas_b == []
    assert n_b == 0
