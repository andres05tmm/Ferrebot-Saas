"""Analítica financiera (F3 reforma dashboard): flujo de dinero, margen por producto, aging CxP,
proyección y calendario. Integración contra base efímera (fixture `tenant`).

Las reglas anti-distorsión que importan: el FIADO no es entrada de dinero (es cartera); el abono a
proveedor generado por un GASTO no cuenta dos veces (dedup ADR 0028); la venta VARIA no entra al
margen por producto; la cobertura de costo es honesta (unidades sin costo no fingen margen).
"""
from datetime import timedelta
from decimal import Decimal

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.service import ReportesService, _promedio_dias_con_movimiento
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _reportes(s: AsyncSession) -> ReportesService:
    return ReportesService(SqlReportesRepository(s))


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cliente', 0) RETURNING id")
        )
    ).scalar_one()


async def _vender(s, *, pid, uid, metodo="efectivo", cantidad="1", cliente_id=None, varia=None):
    lineas = (
        [VentaDetalleCrear(descripcion=varia, cantidad=Decimal("1"), precio_unitario=Decimal(cantidad))]
        if varia
        else [VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cantidad))]
    )
    svc = VentaService(
        SqlVentasRepository(s), fiados=FiadosService(SqlFiadosRepository(s))
    )
    return await svc.registrar_venta(
        VentaCrear(metodo_pago=metodo, cliente_id=cliente_id, lineas=lineas), vendedor_id=uid
    )


async def _factura(s, *, fid, total, dias_atras=0):
    await s.execute(
        text(
            "INSERT INTO facturas_proveedores (id, proveedor, total, pagado, pendiente, estado, fecha) "
            "VALUES (:id, 'Tornillos SA', :t, 0, :t, 'pendiente', :f)"
        ),
        {"id": fid, "t": total, "f": today_co() - timedelta(days=dias_atras)},
    )


async def test_flujo_dinero_excluye_fiado_y_dedup_gasto_abono(tenant, seed_producto):
    """Venta efectivo 20.000 + venta fiada 10.000 + abono de fiado 4.000 + gasto de caja 5.000 que
    SALDA una factura (su abono NO debe duplicarse) + abono directo 3.000 a otra factura."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        cid = await _cliente(s)
        await _factura(s, fid="F-G", total="5000")
        await _factura(s, fid="F-D", total="9000")
        await s.commit()

        caja = CajaService(SqlCajaRepository(s))
        from modules.proveedores.repository import SqlProveedoresRepository
        caja_cxp = CajaService(SqlCajaRepository(s), SqlProveedoresRepository(s))
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        await _vender(s, pid=pid, uid=uid, metodo="efectivo", cantidad="2")      # 20.000
        await _vender(s, pid=pid, uid=uid, metodo="fiado", cliente_id=cid)       # 10.000 (cartera)
        # Abono del cliente fiado: 4.000 (dinero que SÍ entró).
        fiado_id = (
            await s.execute(text("SELECT id FROM fiados WHERE cliente_id=:c"), {"c": cid})
        ).scalar_one()
        await FiadosService(SqlFiadosRepository(s)).abonar(
            fiado_id=fiado_id, monto=Decimal("4000")
        )
        # Gasto que salda F-G (genera SU abono — no debe contarse dos veces).
        await caja_cxp.registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("5000"), concepto="pago tornillos",
            factura_proveedor_id="F-G",
        )
        # Abono directo (sin gasto) a F-D: 3.000.
        await SqlProveedoresRepository(s).crear_abono_y_recalcular(
            factura_id="F-D", monto=Decimal("3000"), fecha=today_co()
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        flujo = await _reportes(s).flujo_dinero(desde=None, hasta=None)

    assert flujo.ventas_por_metodo.get("efectivo") == Decimal("20000.00")
    assert "fiado" not in flujo.ventas_por_metodo
    assert flujo.ventas_fiado == Decimal("10000.00")       # informativo, NO entrada
    assert flujo.abonos_fiados == Decimal("4000.00")
    assert flujo.total_entradas == Decimal("24000.00")     # 20.000 + 4.000
    assert flujo.gastos_por_categoria.get("otros") == Decimal("5000.00")
    assert flujo.abonos_proveedores == Decimal("3000.00")  # el del gasto quedó DEDUPLICADO
    assert flujo.egresos_caja == Decimal("0")              # el egreso del gasto no cuenta aparte
    assert flujo.total_salidas == Decimal("8000.00")
    assert flujo.neto == Decimal("16000.00")


async def test_margen_productos_excluye_varia_y_cobertura_honesta(tenant, seed_producto):
    """Producto con costo 12.000 vendido 2× a 10.000 (sin IVA): margen = 20.000 − 24.000 < 0 pero
    HONESTO. La venta varia no aparece. Cobertura 100% (todas las unidades con costo snapshot)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", iva=0, stock="100")
        await s.execute(
            text("UPDATE productos SET costo_promedio = 12000 WHERE id = :p"), {"p": pid}
        )
        await s.commit()
        await _vender(s, pid=pid, uid=uid, cantidad="2")
        await _vender(s, pid=pid, uid=uid, varia="mano de obra", cantidad="50000")
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        filas = await _reportes(s).margen_productos(desde=None, hasta=None, por="producto", limite=50)

    assert len(filas) == 1   # la varia NO entra
    (f,) = filas
    assert f.ingresos == Decimal("20000.00")
    assert f.cogs == Decimal("24000.00")     # 2 × 12.000 (costo_promedio del seed)
    assert f.margen == Decimal("-4000.00")
    assert f.cobertura_pct == Decimal("100.00")


async def test_aging_cxp_clasifica_tramos_y_semaforo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _factura(s, fid="F-10", total="10000", dias_atras=10)    # 0-30
        await _factura(s, fid="F-45", total="20000", dias_atras=45)    # 31-60
        await _factura(s, fid="F-100", total="30000", dias_atras=100)  # 90+
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        filas = await _reportes(s).aging_cxp()

    (f,) = filas   # mismo proveedor
    assert f.total_pendiente == Decimal("60000.00")
    assert f.d0_30 == Decimal("10000.00")
    assert f.d31_60 == Decimal("20000.00")
    assert f.d90_mas == Decimal("30000.00")
    assert f.semaforo == "rojo"
    assert f.mas_vieja_dias == 100


async def test_calendario_agrega_ventas_y_gastos_del_dia(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="10")
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await _vender(s, pid=pid, uid=uid, cantidad="1")
        await CajaService(SqlCajaRepository(s)).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("2500"), concepto="bolsas"
        )
        await s.commit()

    hoy = today_co()
    async with AsyncSession(tenant.engine) as s:
        dias = await _reportes(s).calendario(anio=hoy.year, mes=hoy.month, vendedor_id=None)

    (d,) = [x for x in dias if x.fecha == hoy]
    assert d.total == Decimal("10000.00")
    assert d.num_ventas == 1
    assert d.gastos == Decimal("2500.00")


async def test_proyeccion_caja_reproduce_la_formula(tenant, seed_producto):
    """Con una venta hoy de 10.000 y un gasto de 2.000: promedio diario = el único día con
    movimiento; proyección = real del mes + promedio × días restantes."""
    import calendar as cal

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="10")
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await _vender(s, pid=pid, uid=uid, cantidad="1")
        await CajaService(SqlCajaRepository(s)).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("2000"), concepto="x"
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        p = await _reportes(s).proyeccion_caja()

    hoy = today_co()
    dias_rest = cal.monthrange(hoy.year, hoy.month)[1] - hoy.day
    assert p.dias_restantes == dias_rest
    assert p.promedio_venta_diaria == Decimal("10000.00")
    assert p.promedio_gasto_diario == Decimal("2000.00")
    assert p.proyeccion_ventas_mes == Decimal("10000.00") + Decimal("10000.00") * dias_rest
    assert p.proyeccion_neto_mes == p.proyeccion_ventas_mes - p.proyeccion_gastos_mes


async def test_hoy_dashboard_agrega_las_senales_del_cockpit(tenant, seed_producto):
    """El agregado /hoy junta: utilidad estimada del día, pedidos en camino, CxP vencida, fiados y
    el avance del inventario progresivo (cuadrados vs activos)."""
    from modules.compras.repository import SqlComprasRepository
    from modules.compras.service import ComprasService
    from modules.inventario.repository import SqlInventarioRepository
    from modules.inventario.service import InventarioService
    from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
    from modules.pedidos_proveedor.schemas import PedidoCrear, ProveedorRef
    from modules.pedidos_proveedor.service import PedidosProveedorService
    from modules.proveedores.repository import SqlProveedoresRepository

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", iva=0, stock="5")
        await s.execute(text("UPDATE productos SET costo_promedio = 4000 WHERE id = :p"), {"p": pid})
        cid = await _cliente(s)
        # CxP vencida hace 10 días (pendiente 9.000) con vencimiento explícito.
        await s.execute(
            text(
                "INSERT INTO facturas_proveedores (id, proveedor, total, pagado, pendiente, estado, "
                "fecha, fecha_vencimiento) VALUES ('F-V', 'Tornillos SA', 9000, 0, 9000, 'pendiente', "
                ":f, :v)"
            ),
            {"f": today_co() - timedelta(days=40), "v": today_co() - timedelta(days=10)},
        )
        await s.commit()

        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await _vender(s, pid=pid, uid=uid, cantidad="2")                       # 20.000, COGS 8.000
        await _vender(s, pid=pid, uid=uid, metodo="fiado", cliente_id=cid)     # fiado 10.000
        await CajaService(SqlCajaRepository(s)).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("3000"), concepto="x"
        )
        # Un pedido a proveedor en camino y un producto cuadrado (conteo físico).
        pedidos = PedidosProveedorService(
            SqlPedidosProveedorRepository(s),
            compras=ComprasService(SqlComprasRepository(s)),
            compras_repo=SqlComprasRepository(s),
            proveedores=SqlProveedoresRepository(s),
            caja=CajaService(SqlCajaRepository(s)),
            inventario=InventarioService(SqlInventarioRepository(s)),
        )
        await pedidos.crear(
            PedidoCrear(proveedor=ProveedorRef(nombre="Eternit"), descripcion="10 tejas"),
            usuario_id=uid,
        )
        await InventarioService(SqlInventarioRepository(s)).contar(
            producto_id=pid, cantidad_contada=Decimal("2"), usuario_id=uid
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        d = await _reportes(s).hoy_dashboard()

    assert d.caja_abierta is True
    assert d.ingresos_hoy == Decimal("30000.00")           # 20.000 contado + 10.000 fiado (ingreso P&L)
    assert d.gastos_hoy == Decimal("3000.00")
    assert d.utilidad_estimada == Decimal("15000.00")      # 30.000 − 12.000 COGS − 3.000
    assert d.pedidos_en_camino == 1
    assert d.pedido_mas_viejo_horas is not None
    assert d.cxp_vencidas == 1 and d.cxp_monto_vencido == Decimal("9000.00")
    assert d.fiados_total == Decimal("10000.00")
    assert d.productos_activos == 1 and d.productos_cuadrados == 1


def test_promedio_dias_con_movimiento_ignora_ceros():
    from datetime import date

    serie = [(date(2026, 7, 1), Decimal("100")), (date(2026, 7, 2), Decimal("0")),
             (date(2026, 7, 3), Decimal("200"))]
    assert _promedio_dias_con_movimiento(serie) == Decimal("150")
    assert _promedio_dias_con_movimiento([]) == Decimal("0")
