"""Estados financieros del ledger (ADR 0030): cuadre del balance de comprobación + cruce con el P&L
simple (`modules/reportes`). Verificación end-to-end sobre un tenant sembrado.

Ejercita venta contado + venta fiado + abono + gasto + compra + devolución y verifica:
- balance de comprobación cuadra (Σ débitos = Σ créditos global);
- balance general cierra (activos = pasivos + patrimonio + utilidad);
- el ingreso (413505) y el costo de ventas (613505) del ledger coinciden con el P&L simple.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co, today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.contabilidad.estados import EstadosService
from modules.contabilidad.fuente_repository import FuenteContableRepository
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.proyector import Proyector
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.schemas import DevolucionCrear, DevolucionLineaCrear
from modules.devoluciones.service import DevolucionesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.reportes.repository import SqlReportesRepository
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


async def _usuario(s):
    return (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))).scalar_one()


async def _cliente(s):
    return (await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('C',0) RETURNING id"))).scalar_one()


async def _producto(s, *, precio="20000", costo="12000", stock="100"):
    pid = (await s.execute(
        text("INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
             "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',:pv,:pc,:cp,19,false,true) RETURNING id"),
        {"pv": precio, "pc": costo, "cp": costo},
    )).scalar_one()
    await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
                    {"p": pid, "s": stock})
    return pid


def _venta(pid, cant, *, metodo="efectivo", cliente_id=None):
    return VentaCrear(metodo_pago=metodo, cliente_id=cliente_id,
                      lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cant))])


def _dev_svc(s):
    return DevolucionesService(
        SqlDevolucionesRepository(s), caja=SqlCajaRepository(s),
        fiados=FiadosService(SqlFiadosRepository(s)), notas=None,
    )


async def test_e2e_balance_cuadra_y_cruza_con_pl(tenant):
    # 1) Sembrar operaciones: venta contado, venta fiado, abono, gasto, compra, devolución total.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        vs = VentaService(SqlVentasRepository(s), fiados=FiadosService(SqlFiadosRepository(s)))
        v_contado = (await vs.registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        v_fiado = (await vs.registrar_venta(_venta(pid, "2", metodo="fiado", cliente_id=cid), vendedor_id=uid)).venta
        await CajaService(SqlCajaRepository(s)).registrar_gasto(
            usuario_id=uid, categoria="servicios", monto=Decimal("15000"), concepto="luz"
        )
        # Compra (repone inventario) con detalle → base al inventario.
        compra_id = (await s.execute(
            text("INSERT INTO compras (proveedor_id, total) VALUES (NULL, 240000) RETURNING id")
        )).scalar_one()
        await s.execute(text(
            "INSERT INTO compras_detalle (compra_id, producto_id, cantidad, costo) VALUES (:c,:p,20,12000)"),
            {"c": compra_id, "p": pid})
        await s.commit()

    # Abono al fiado del cliente (paga parte de su deuda).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        fiado_id = (await s.execute(
            text("SELECT id FROM fiados WHERE venta_id=:v"), {"v": v_fiado.id}
        )).scalar_one()
        await FiadosService(SqlFiadosRepository(s)).abonar(fiado_id=fiado_id, monto=Decimal("10000"))
        await s.commit()

    # Devolución total de la venta de contado.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        dev = await _dev_svc(s).devolver(DevolucionCrear(venta_id=v_contado.id), usuario_id=uid)
        dev_id = dev.devolucion.id
        await s.commit()

    # 2) Proyectar TODO al ledger vía backfill.
    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlContabilidadRepository(s)
        await repo.asegurar_puc()
        proj = Proyector(LedgerService(repo), FuenteContableRepository(s))
        await proj.backfill(inicio)
        await s.commit()

    # 3) Balance de comprobación cuadra + balance general cierra.
    async with AsyncSession(tenant.engine) as s:
        estados = EstadosService(SqlContabilidadRepository(s))
        bc = await estados.balance_comprobacion()
        assert bc.cuadra and bc.total_debitos == bc.total_creditos
        bg = await estados.balance_general()
        assert bg.cuadra
        er = await estados.estado_resultados(inicio=inicio, fin=fin)

        # 4) Cruce con el P&L simple.
        pl = await SqlReportesRepository(s).estado_resultados(inicio=inicio, fin=fin)

    # Ingreso del ledger (413505) == ingresos del P&L (Σ subtotal de ventas completadas).
    ingreso_413505 = next(f.valor for f in er.ingresos if f.codigo == "413505")
    assert ingreso_413505 == pl.ingresos
    # Costo de ventas del ledger (613505) == COGS del P&L (SALIDA − DEVOLUCION).
    costo_613505 = next(f.valor for f in er.costos if f.codigo == "613505")
    assert costo_613505 == pl.costo_ventas
    # Gastos del ledger (clase 5) == gastos del P&L (gasto simple, sin CxP).
    assert er.total_gastos == pl.gastos

    # 5) El saldo_cache reconstruido coincide con el balance (fuente = líneas).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlContabilidadRepository(s)
        antes = {(r.cuenta_id, r.periodo_id): r.saldo for r in await repo.saldos_cache()}
        from core.config.timezone import now_co
        await repo.recomputar_saldos(now_co())
        await s.commit()
    async with AsyncSession(tenant.engine) as s:
        despues = {(r.cuenta_id, r.periodo_id): r.saldo for r in await SqlContabilidadRepository(s).saldos_cache()}
    assert antes == despues   # el cache incremental == recomputado desde las líneas
    assert dev_id  # devolución existió
