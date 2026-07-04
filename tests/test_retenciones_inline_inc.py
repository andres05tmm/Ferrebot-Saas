"""Retenciones cableadas INLINE en venta/compra + INC opcional al total (ADR 0027, feat retenciones-inline).

Cubre:
- Invariante crítico (idempotencia, TDD): reintentar una venta por `idempotency_key` NO duplica los
  renglones de `retenciones_documento` (el replay ni siquiera re-aplica; el UPSERT lo respaldaría igual).
- Cableado inline: al registrar venta/compra con la feature activa, los renglones se persisten en la
  MISMA transacción (commit=False) y el total del documento queda INTACTO.
- INC opcional al total: con la config `inc_al_total` activa, el INC SUMA al total del documento
  (`total_con_inc`), sin tocar la tabla `ventas`; apagada, se informa aparte como antes.
- Compatibilidad con el proyector contable (ADR 0030): proyectar tras el cableado inline cuadra
  (débitos=créditos), es idempotente y NO asienta el INC (decisión v1).
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.compras.schemas import CompraCrear, CompraItemCrear, ProveedorRef
from modules.compras.service import ComprasService
from modules.compras.repository import SqlComprasRepository
from modules.contabilidad.fuente_repository import FuenteContableRepository
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.proyector import Proyector
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.schemas import ReglaUpsert
from modules.retenciones.service import RetencionesService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


# ── helpers ──────────────────────────────────────────────────────────────────
async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Ven','vendedor') RETURNING id"))
    ).scalar_one()


async def _producto(s: AsyncSession, *, precio="1000000", costo="600000", stock="100") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, "
                "costo_promedio, iva, permite_fraccion, activo) "
                "VALUES ('Cemento','unidad',:pv,:pc,:cp,19,false,true) RETURNING id"
            ),
            {"pv": precio, "pc": costo, "cp": costo},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _seed_retenciones(s: AsyncSession) -> None:
    """retefuente 2.5% + reteIVA 15% (retenciones verdaderas) sobre un UVT del 2026."""
    svc = RetencionesService(SqlRetencionesRepository(s))
    await svc.upsert_regla(ReglaUpsert(tipo="uvt", concepto="2026", tarifa=Decimal("49799")))
    await svc.upsert_regla(ReglaUpsert(tipo="retefuente", concepto="compras", tarifa=Decimal("2.5")))
    await svc.upsert_regla(ReglaUpsert(tipo="reteiva", concepto="reteiva", tarifa=Decimal("15")))


def _venta(pid, *, metodo="efectivo", key=None):
    return VentaCrear(
        metodo_pago=metodo, origen="web", idempotency_key=key,
        lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))],
    )


def _venta_service(s: AsyncSession) -> VentaService:
    return VentaService(
        SqlVentasRepository(s),
        retenciones=RetencionesService(SqlRetencionesRepository(s)),
    )


async def _count_renglones(engine, doc_id=None) -> int:
    async with AsyncSession(engine) as s:
        q = "SELECT count(*) FROM retenciones_documento"
        if doc_id is not None:
            q += f" WHERE doc_id={doc_id}"
        return (await s.execute(text(q))).scalar_one()


# ── INVARIANTE crítico: idempotencia del cálculo/UPSERT en reintentos de venta ─
async def test_reintento_venta_por_idempotency_no_duplica_renglones(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        await _seed_retenciones(s)
        await s.commit()

    # 1ª venta con key: crea la venta y aplica retenciones inline (misma tx).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _venta_service(s).registrar_venta(_venta(pid, key="k1"), vendedor_id=uid)
        await s.commit()
    n_tras_primera = await _count_renglones(tenant.engine)
    assert r1.replay is False
    assert n_tras_primera == 2  # retefuente + reteiva

    # Reintento con la MISMA key + mismo payload: replay → NO re-aplica → no duplica.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _venta_service(s).registrar_venta(_venta(pid, key="k1"), vendedor_id=uid)
        await s.commit()
    assert r2.replay is True
    assert r2.venta.id == r1.venta.id
    assert await _count_renglones(tenant.engine) == 2  # sigue en 2 (no 4)


# ── Cableado inline: renglones atómicos con la venta, total intacto ───────────
async def test_venta_inline_persiste_en_su_transaccion_y_total_intacto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        await _seed_retenciones(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _venta_service(s).registrar_venta(_venta(pid), vendedor_id=uid)
        venta_id = res.venta.id
        total_creado = res.venta.total
        # Antes del commit externo los renglones existen en ESTA sesión (misma tx, commit=False).
        n_en_tx = (
            await s.execute(
                text("SELECT count(*) FROM retenciones_documento WHERE doc_id=:i"), {"i": venta_id}
            )
        ).scalar_one()
        assert n_en_tx == 2
        await s.commit()  # el commit externo (tenant_session/POS) los persiste junto con la venta

    async with AsyncSession(tenant.engine) as s:
        total = (
            await s.execute(text("SELECT total FROM ventas WHERE id=:i"), {"i": venta_id})
        ).scalar_one()
        n = (
            await s.execute(
                text("SELECT count(*) FROM retenciones_documento WHERE doc_id=:i"), {"i": venta_id}
            )
        ).scalar_one()
    assert total == total_creado  # la venta NO cambió (invariante: retención = menor pago, no menor venta)
    assert n == 2


async def test_sin_feature_no_se_calculan_retenciones(tenant):
    # VentaService sin el aplicador inyectado (tenant sin la feature `retenciones`) → cero renglones.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        await _seed_retenciones(s)   # aunque haya config, sin aplicador no corre nada
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid), vendedor_id=uid)
        await s.commit()
    assert await _count_renglones(tenant.engine) == 0


# ── Compra inline (agente retenedor) ─────────────────────────────────────────
async def test_compra_inline_persiste_renglones(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        await _seed_retenciones(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = ComprasService(
            SqlComprasRepository(s), retenciones=RetencionesService(SqlRetencionesRepository(s))
        )
        datos = CompraCrear(
            proveedor=ProveedorRef(nombre="Proveedor X"),
            items=[CompraItemCrear(producto_id=pid, cantidad=Decimal("1"), costo=Decimal("500000"))],
        )
        res = await svc.registrar(datos, usuario_id=None)
        await s.commit()

    # La compra sin desglose fiscal toma el total como base gravable (IVA 0): retefuente aplica, reteIVA no.
    async with AsyncSession(tenant.engine) as s:
        filas = (
            await s.execute(
                text("SELECT tipo FROM retenciones_documento WHERE doc_tipo='compra' AND doc_id=:i"),
                {"i": res.compra.id},
            )
        ).scalars().all()
    assert "retefuente" in filas


# ── INC opcional al total del documento (ADR 0027 D5) ────────────────────────
async def _venta_directa(s: AsyncSession, *, total="1190000.00", subtotal="1000000.00",
                         impuestos="190000.00") -> int:
    uid = await _usuario(s)
    return (
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                "metodo_pago, estado, origen) VALUES (1,:v,:f,:s,:i,:t,'efectivo','completada','web') "
                "RETURNING id"
            ),
            {"v": uid, "f": now_co(), "s": subtotal, "i": impuestos, "t": total},
        )
    ).scalar_one()


async def test_inc_suma_al_total_cuando_la_config_lo_activa(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        venta_id = await _venta_directa(s)
        await s.commit()
        svc = RetencionesService(SqlRetencionesRepository(s))
        await svc.upsert_regla(ReglaUpsert(tipo="inc", concepto="general", tarifa=Decimal("8")))
        await svc.upsert_regla(ReglaUpsert(tipo="inc_al_total", concepto="global", activo=True))
        resumen = await svc.aplicar_a_venta(venta_id)

    # INC 8% de 1.000.000 = 80.000 → total_con_inc = 1.190.000 + 80.000 = 1.270.000. Sin retenciones.
    assert resumen is not None
    assert resumen.inc_al_total is True
    assert resumen.total_documento == Decimal("1190000.00")     # la tabla NO cambia
    assert resumen.total_inc == Decimal("80000.00")
    assert resumen.total_con_inc == Decimal("1270000.00")       # INC SUMA al total (fiscal)
    assert resumen.neto_a_recibir == Decimal("1270000.00")      # sin retenciones: neto = total_con_inc

    async with AsyncSession(tenant.engine) as s:
        total = (
            await s.execute(text("SELECT total FROM ventas WHERE id=:i"), {"i": venta_id})
        ).scalar_one()
    assert total == Decimal("1190000.00")   # invariante: ventas.total intacto


async def test_inc_no_suma_al_total_sin_la_config(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        venta_id = await _venta_directa(s)
        await s.commit()
        svc = RetencionesService(SqlRetencionesRepository(s))
        await svc.upsert_regla(ReglaUpsert(tipo="inc", concepto="general", tarifa=Decimal("8")))
        resumen = await svc.aplicar_a_venta(venta_id)   # sin la fila inc_al_total

    assert resumen is not None
    assert resumen.inc_al_total is False
    assert resumen.total_inc == Decimal("80000.00")            # se registra/informa aparte
    assert resumen.total_con_inc == Decimal("1190000.00")      # NO suma
    assert resumen.neto_a_recibir == Decimal("1190000.00")


# ── Compatibilidad con el proyector contable (ADR 0030) ──────────────────────
async def test_proyector_tras_inline_cuadra_es_idempotente_y_no_asienta_inc(tenant):
    # Venta con retenciones verdaderas + INC, todas cableadas inline; luego el proyector.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s)
        await _seed_retenciones(s)
        svc = RetencionesService(SqlRetencionesRepository(s))
        await svc.upsert_regla(ReglaUpsert(tipo="inc", concepto="general", tarifa=Decimal("8")))
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _venta_service(s).registrar_venta(_venta(pid), vendedor_id=uid)
        await s.commit()
    venta_id = res.venta.id

    # 3 renglones persistidos: retefuente + reteiva + inc.
    assert await _count_renglones(tenant.engine, venta_id) == 3

    async def _proyector(s: AsyncSession) -> Proyector:
        repo = SqlContabilidadRepository(s)
        await repo.asegurar_puc()
        return Proyector(LedgerService(repo), FuenteContableRepository(s))

    # Proyecta la venta + todas sus retenciones (backfill idempotente).
    from datetime import timedelta
    desde = now_co() - timedelta(days=1)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await (await _proyector(s)).backfill(desde)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await (await _proyector(s)).backfill(desde)   # segunda corrida
        await s.commit()

    # 2ª corrida no crea asientos nuevos de retención (replay).
    assert r2.creados.get("retencion", 0) == 0

    async with AsyncSession(tenant.engine) as s:
        # El INC NO genera asiento (v1): solo retefuente + reteiva → 2 asientos de retención.
        n_ret = (
            await s.execute(
                text("SELECT count(*) FROM journal_entry WHERE origen_tipo='retencion'")
            )
        ).scalar_one()
        # Todo asiento cuadra: Σdébitos = Σcréditos por línea posteada.
        deb = (
            await s.execute(
                text("SELECT coalesce(sum(amount),0) FROM journal_line WHERE direction='debit'")
            )
        ).scalar_one()
        cred = (
            await s.execute(
                text("SELECT coalesce(sum(amount),0) FROM journal_line WHERE direction='credit'")
            )
        ).scalar_one()
    assert n_ret == 2                      # retefuente + reteiva; el INC no se asienta
    assert Decimal(deb) == Decimal(cred)   # el ledger cuadra tras el cableado inline
