"""Flujo de dinero: cada egreso con su procedencia (cierre del requisito "de dónde sale la plata").

El reporte ya sumaba los egresos de caja, pero todos en un mismo saco: no se sabía si esa plata se
fue en mercancía, en un anticipo o en un abono al proveedor. Ahora se desglosa por el prefijo de
`caja_movimientos.referencia` (`pedido:`, `compra:`, `abono:`, sin referencia → manual).

Invariante que se prueba: el desglose es un DETALLE de lo ya contado — la suma de los orígenes
cuadra con `egresos_caja` + los abonos, sin doble conteo.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import AbonoCrear, FacturaProveedorCrear
from modules.proveedores.service import ProveedoresService
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.service import ReportesService


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
        )
    ).scalar_one()


async def test_egresos_se_desglosan_por_procedencia_sin_doble_conteo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        caja = CajaService(SqlCajaRepository(s))
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("1000000"))
        await s.commit()

        # Pago al hacer un pedido, pago de mercancía al recibir y un retiro manual.
        await caja.registrar_movimiento(
            usuario_id=uid, tipo="egreso", monto=Decimal("50000"),
            concepto="Pago de contado pedido proveedor #1", referencia="pedido:1",
        )
        await caja.registrar_movimiento(
            usuario_id=uid, tipo="egreso", monto=Decimal("80000"),
            concepto="Pedido proveedor #2 — pago mercancía", referencia="compra:7",
        )
        await caja.registrar_movimiento(
            usuario_id=uid, tipo="egreso", monto=Decimal("15000"),
            concepto="Retiro del dueño", referencia=None,
        )
        # Un abono a proveedor pagado desde la caja (postea su propio egreso).
        prov = ProveedoresService(SqlProveedoresRepository(s), caja=caja)
        await prov.crear_factura(
            FacturaProveedorCrear(
                id="F-9", proveedor="Ferrisariato", total=Decimal("200000"), fecha=today_co()
            ),
            usuario_id=uid,
        )
        await prov.registrar_abono(
            AbonoCrear(factura_id="F-9", monto=Decimal("30000"), origen_fondos="caja"),
            usuario_id=uid,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        flujo = await ReportesService(SqlReportesRepository(s)).flujo_dinero(desde=None, hasta=None)

    assert flujo.egresos_por_origen == {
        "Anticipos y pagos al pedir": Decimal("50000.00"),
        "Pago de mercancía": Decimal("80000.00"),
        "Movimientos manuales de caja": Decimal("15000.00"),
    }
    # El abono no se cuenta dos veces: viaja en su propio bucket, fuera de `egresos_caja`.
    assert flujo.egresos_caja == Decimal("145000.00")          # 50.000 + 80.000 + 15.000
    assert flujo.abonos_proveedores == Decimal("30000.00")
    assert sum(flujo.egresos_por_origen.values()) == flujo.egresos_caja
    assert flujo.total_salidas == Decimal("175000.00")
