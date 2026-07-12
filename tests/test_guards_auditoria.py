"""Guards agregados por la auditoría de lógica del dashboard PIM (2026-07).

Cubre, capa por capa, los huecos que la auditoría confirmó:
  - Ventana del parte de horas manual (router): nunca futuro; hasta 3 días atrás.
  - Fechas no futuras en abonos/facturas de proveedor y compras (schemas).
  - Obra LIQUIDADA es snapshot inmutable: no admite partes de horas ni gastos imputados.
  - Máquina en MANTENIMIENTO/DAÑADA/BAJA no se puede activar en vivo.
  - PATCH de asignación no puede dejar `fecha_fin < fecha_inicio` (rango invertido invisible).
  - Mantenimiento con fecha futura rechazado (apagaría las alertas del panel).
  - Periodos de nómina con rangos disjuntos (solape = doble liquidación de la misma asistencia).

Unitarios arriba (sin BD); integración abajo contra el Postgres efímero (fixture `tenant`).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.caja.errors import ObraNoImputable
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.schemas import CompraCrear, CompraItemCrear, ProveedorRef
from modules.maquinaria.errors import (
    FechaMantenimientoInvalida,
    MaquinaNoOperable,
    ObraNoAsignable,
    RangoAsignacionInvalido,
)
from modules.maquinaria.operacion_service import construir_operacion_service
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.router import VENTANA_DIAS_PARTE, _validar_fecha_parte
from modules.maquinaria.schemas import (
    AsignacionMaquinaActualizar,
    MantenimientoCrear,
    RegistroHorasCrear,
)
from modules.maquinaria.service import MaquinariaService
from modules.nomina.errors import PeriodoSolapado
from modules.nomina.repository import SqlNominaRepository
from modules.nomina.schemas import PeriodoCrear
from modules.nomina.service import NominaService
from modules.proveedores.schemas import AbonoCrear, FacturaProveedorCrear


# ---- Unitarios (sin BD) -----------------------------------------------------------------------

def test_ventana_fecha_parte():
    """Futuro → 422 siempre; más de VENTANA_DIAS_PARTE atrás → 422; hoy y el borde de la ventana → OK."""
    hoy = today_co()
    with pytest.raises(HTTPException) as exc:
        _validar_fecha_parte(hoy + timedelta(days=1))
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        _validar_fecha_parte(hoy - timedelta(days=VENTANA_DIAS_PARTE + 1))
    assert exc.value.status_code == 422

    _validar_fecha_parte(hoy)                                     # hoy
    _validar_fecha_parte(hoy - timedelta(days=1))                 # ayer (el parte olvidado)
    _validar_fecha_parte(hoy - timedelta(days=VENTANA_DIAS_PARTE))  # borde de la ventana


def test_abono_y_factura_proveedor_fecha_futura_rechazada():
    manana = today_co() + timedelta(days=1)
    with pytest.raises(ValidationError):
        AbonoCrear(factura_id="F-1", monto=Decimal("1000"), fecha=manana)
    with pytest.raises(ValidationError):
        FacturaProveedorCrear(id="F-1", proveedor="Cantera", total=Decimal("1000"), fecha=manana)
    # Hoy y pasado siguen siendo válidos (y el vencimiento futuro también).
    AbonoCrear(factura_id="F-1", monto=Decimal("1000"), fecha=today_co())
    FacturaProveedorCrear(
        id="F-2", proveedor="Cantera", total=Decimal("1000"),
        fecha=today_co(), fecha_vencimiento=manana,
    )


def test_compra_fecha_futura_rechazada():
    item = CompraItemCrear(producto_id=1, cantidad=Decimal("1"), costo=Decimal("100"))
    with pytest.raises(ValidationError):
        CompraCrear(
            proveedor=ProveedorRef(nombre="Cantera"), items=[item],
            fecha=today_co() + timedelta(days=1),
        )
    CompraCrear(proveedor=ProveedorRef(nombre="Cantera"), items=[item], fecha=today_co())


# ---- Integración (Postgres efímero) ------------------------------------------------------------

def _maq_service(s: AsyncSession) -> MaquinariaService:
    return MaquinariaService(SqlMaquinasRepository(s))


async def _seed_maquina_obra(s: AsyncSession) -> tuple[int, int]:
    """Máquina + cliente + obra + asignación activa desde 2026-01-01 sin fin (cubre hoy)."""
    cliente_id = (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()
    obra_id = (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Vía La Paz') RETURNING id"),
            {"c": cliente_id},
        )
    ).scalar_one()
    maquina_id = (
        await s.execute(
            text(
                "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
                "VALUES ('M-1', 'Vibrocompactador', 'compactador', 150000) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas, activa) "
            "VALUES (:m, :o, '2026-01-01', 160000, 5, true)"
        ),
        {"m": maquina_id, "o": obra_id},
    )
    await s.flush()
    return maquina_id, obra_id


async def test_parte_sobre_obra_liquidada_rechazado(tenant):
    """Una obra LIQUIDADA es un snapshot inmutable: registrar horas contra ella → ObraNoAsignable (409)."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, obra_id = await _seed_maquina_obra(s)
        await s.execute(text("UPDATE obras SET estado = 'LIQUIDADA' WHERE id = :o"), {"o": obra_id})
        with pytest.raises(ObraNoAsignable) as exc:
            await _maq_service(s).registrar_horas(
                maquina_id,
                RegistroHorasCrear(obra_id=obra_id, fecha=date(2026, 1, 2), horas_trabajadas=Decimal("6")),
            )
        assert exc.value.motivo == "liquidada"


async def test_gasto_sobre_obra_liquidada_o_inexistente_rechazado(tenant):
    """El gasto imputado valida la obra ANTES del insert: liquidada → 409; inexistente → 404 (antes: FK 500)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        _, obra_id = await _seed_maquina_obra(s)
        await s.execute(text("UPDATE obras SET estado = 'LIQUIDADA' WHERE id = :o"), {"o": obra_id})
        await s.commit()
        svc = CajaService(SqlCajaRepository(s))
        await svc.abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))

        with pytest.raises(ObraNoImputable) as exc:
            await svc.registrar_gasto(
                usuario_id=uid, categoria="otros", monto=Decimal("5000"),
                concepto="combustible", obra_id=obra_id,
            )
        assert exc.value.motivo == "liquidada"

        with pytest.raises(ObraNoImputable) as exc:
            await svc.registrar_gasto(
                usuario_id=uid, categoria="otros", monto=Decimal("5000"),
                concepto="combustible", obra_id=999999,
            )
        assert exc.value.motivo == "inexistente"


async def test_iniciar_operacion_maquina_no_operable(tenant):
    """Una máquina en MANTENIMIENTO (o dañada/de baja) no se activa en vivo aunque tenga asignación."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, _ = await _seed_maquina_obra(s)
        await s.execute(
            text("UPDATE maquinas SET estado = 'MANTENIMIENTO' WHERE id = :m"), {"m": maquina_id}
        )
        with pytest.raises(MaquinaNoOperable):
            await construir_operacion_service(s).iniciar(maquina_id)


async def test_patch_asignacion_rango_invertido_rechazado(tenant):
    """PATCH con fecha_fin anterior a fecha_inicio → RangoAsignacionInvalido (dato corrupto invisible)."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, _ = await _seed_maquina_obra(s)
        asig_id = (
            await s.execute(
                text("SELECT id FROM asignaciones_maquina_obra WHERE maquina_id = :m"), {"m": maquina_id}
            )
        ).scalar_one()
        with pytest.raises(RangoAsignacionInvalido):
            await _maq_service(s).actualizar_asignacion(
                maquina_id, asig_id,
                AsignacionMaquinaActualizar(activa=False, fecha_fin=date(2025, 12, 31)),
            )


async def test_mantenimiento_fecha_futura_rechazado(tenant):
    """Un mantenimiento se registra cuando ya ocurrió: fecha futura → FechaMantenimientoInvalida (422)."""
    async with AsyncSession(tenant.engine) as s:
        maquina_id, _ = await _seed_maquina_obra(s)
        with pytest.raises(FechaMantenimientoInvalida):
            await _maq_service(s).crear_mantenimiento(
                maquina_id,
                MantenimientoCrear(
                    tipo="PREVENTIVO", descripcion="typo de año",
                    fecha=today_co() + timedelta(days=30),
                ),
            )


async def test_periodo_nomina_solapado_rechazado(tenant):
    """Dos periodos que comparten días liquidarían la misma asistencia dos veces → PeriodoSolapado (409)."""
    async with AsyncSession(tenant.engine) as s:
        await s.execute(
            text(
                "INSERT INTO parametros_legales "
                "(vigente_desde, smmlv, auxilio_transporte, salud_empleado_pct, pension_empleado_pct, "
                " salud_empleador_pct, pension_empleador_pct, arl_pct) "
                "VALUES ('2026-01-01', 1750905, 249095, 0.04, 0.04, 0.085, 0.12, 0.0522)"
            )
        )
        await s.flush()
        svc = NominaService(SqlNominaRepository(s))
        await svc.crear_periodo(
            PeriodoCrear(tipo="QUINCENAL", fecha_inicio=date(2026, 7, 1), fecha_fin=date(2026, 7, 15))
        )
        with pytest.raises(PeriodoSolapado):
            await svc.crear_periodo(
                PeriodoCrear(tipo="QUINCENAL", fecha_inicio=date(2026, 7, 10), fecha_fin=date(2026, 7, 20))
            )
        # Un periodo disjunto sí pasa (el guard no bloquea de más).
        p2 = await svc.crear_periodo(
            PeriodoCrear(tipo="QUINCENAL", fecha_inicio=date(2026, 7, 16), fecha_fin=date(2026, 7, 31))
        )
        assert p2.id is not None
