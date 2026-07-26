"""Estado por proveedor y estado de cuenta con saldo corrido.

Es lo que el dueño pidió ver en un solo lugar: cuánto le debe a cada proveedor, qué le viene en
camino y el movimiento a movimiento (facturas y abonos) con el saldo acumulado — el "detailed
ledger" de los contables. Lo que se prueba: los agregados por proveedor, el corrido que cuadra con
el pendiente, el saldo anterior al rango y la antigüedad de la deuda.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import today_co
from modules.proveedores.errors import ProveedorInexistente
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import AbonoCrear, FacturaProveedorCrear
from modules.proveedores.service import ProveedoresService


async def _proveedor(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO proveedores (nombre) VALUES (:n) RETURNING id"), {"n": nombre}
        )
    ).scalar_one()


def _svc(s: AsyncSession) -> ProveedoresService:
    return ProveedoresService(SqlProveedoresRepository(s))


async def test_estado_de_cuenta_lleva_saldo_corrido_y_antiguedad(tenant):
    hoy = today_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _proveedor(s, "Ferrisariato")
        await s.commit()
        svc = _svc(s)

        # Deuda vieja (hace 100 días) parcialmente abonada, y una reciente.
        await svc.crear_factura(
            FacturaProveedorCrear(
                id="F-VIEJA", proveedor="Ferrisariato", total=Decimal("100000"),
                fecha=hoy - timedelta(days=100),
            ),
            usuario_id=None,
        )
        await svc.crear_factura(
            FacturaProveedorCrear(
                id="F-NUEVA", proveedor="Ferrisariato", total=Decimal("50000"),
                fecha=hoy - timedelta(days=10), fecha_vencimiento=hoy - timedelta(days=1),
            ),
            usuario_id=None,
        )
        await svc.registrar_abono(
            AbonoCrear(
                factura_id="F-VIEJA", monto=Decimal("30000"), fecha=hoy - timedelta(days=90),
                origen_fondos="banco",
            ),
            usuario_id=None,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        estado = await _svc(s).estado_cuenta(pid, desde=None, hasta=None)

    assert estado.proveedor_nombre == "Ferrisariato"
    assert estado.saldo_pendiente == Decimal("120000.00")     # 100.000 − 30.000 + 50.000
    assert estado.vencido == Decimal("50000.00")              # solo la que tiene vencimiento pasado
    assert estado.aging["90+"] == Decimal("70000.00")         # la vieja, con su abono aplicado
    assert estado.aging["0-30"] == Decimal("50000.00")

    # El corrido: cada línea deja el saldo como quedó, y la última cuadra con el pendiente.
    corrido = [(m.tipo, m.cargo, m.abono, m.saldo) for m in estado.movimientos]
    assert corrido == [
        ("factura", Decimal("100000.00"), Decimal("0.00"), Decimal("100000.00")),
        ("abono", Decimal("0.00"), Decimal("30000.00"), Decimal("70000.00")),
        ("factura", Decimal("50000.00"), Decimal("0.00"), Decimal("120000.00")),
    ]
    assert estado.movimientos[1].medio == "banco"             # de dónde salió la plata
    assert estado.saldo_anterior == Decimal("0.00")


async def test_el_rango_arranca_del_saldo_anterior(tenant):
    """Pidiendo solo el último mes, el corrido no empieza en cero: arranca en lo que ya se debía."""
    hoy = today_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _proveedor(s, "Ferrisariato")
        await s.commit()
        await _svc(s).crear_factura(
            FacturaProveedorCrear(
                id="F-1", proveedor="Ferrisariato", total=Decimal("80000"),
                fecha=hoy - timedelta(days=60),
            ),
            usuario_id=None,
        )
        await _svc(s).crear_factura(
            FacturaProveedorCrear(
                id="F-2", proveedor="Ferrisariato", total=Decimal("20000"), fecha=hoy,
            ),
            usuario_id=None,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        estado = await _svc(s).estado_cuenta(pid, desde=hoy - timedelta(days=15), hasta=hoy)

    assert estado.saldo_anterior == Decimal("80000.00")
    assert [m.referencia for m in estado.movimientos] == ["F-2"]
    assert estado.movimientos[0].saldo == Decimal("100000.00")   # 80.000 previos + 20.000


async def test_lista_de_proveedores_trae_deuda_y_ritmo(tenant):
    hoy = today_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        con_deuda = await _proveedor(s, "Ferrisariato")
        await _proveedor(s, "Sin Deuda SAS")
        await s.commit()
        await _svc(s).crear_factura(
            FacturaProveedorCrear(
                id="F-9", proveedor="Ferrisariato", total=Decimal("45000"),
                fecha=hoy - timedelta(days=5), fecha_vencimiento=hoy - timedelta(days=2),
            ),
            usuario_id=None,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        filas = await _svc(s).estado_proveedores()

    por_id = {f.id: f for f in filas}
    assert por_id[con_deuda].saldo_pendiente == Decimal("45000.00")
    assert por_id[con_deuda].vencido == Decimal("45000.00")
    assert por_id[con_deuda].facturas_pendientes == 1
    # El proveedor sin movimientos aparece igual (es el directorio), en ceros.
    sin_deuda = next(f for f in filas if f.nombre == "Sin Deuda SAS")
    assert sin_deuda.saldo_pendiente == Decimal("0")
    assert sin_deuda.pedidos_en_camino == 0 and sin_deuda.lead_time_promedio_horas is None


async def test_proveedor_inexistente(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(ProveedorInexistente):
            await _svc(s).estado_cuenta(9999, desde=None, hasta=None)
