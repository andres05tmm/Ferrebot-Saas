"""Repositorio de nómina: único lugar con SQL del módulo (regla no negociable #2).

Persiste el resultado del motor puro (`services.calculations.nomina`), nunca recalcula dinero aquí. La
sesión del tenant ES la transacción; el repo solo hace `flush` (la frontera de commit es del llamador).

Idempotencia (invariante test-primero):
  - `upsert_detalle` respeta el UNIQUE(periodo_id, trabajador_id): re-liquidar ACTUALIZA el detalle en
    vez de insertar otro, y NUNCA pisa `cune_dian` (dato de la Fase 7).
  - `reemplazar_prorrateos` borra los prorrateos previos del (periodo, trabajador) antes de insertar los
    nuevos: re-liquidar deja exactamente un juego de filas, sin duplicar.

Importa los modelos de `modules.trabajadores` y `modules.obra` en modo SOLO LECTURA (patrón de la Ola A:
cada fase persiste por su propio repo importando el modelo congelado).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.nomina.models import (
    DetalleLiquidacion,
    ParametrosLegales,
    PeriodoNomina,
    ProrrateoNominaObra,
)
from modules.nomina.schemas import AsistenciaCrear, PeriodoCrear
from modules.obra.models import Obra
from modules.trabajadores.models import RegistroAsistencia, Trabajador
from services.calculations.nomina import Liquidacion, ProrrateoObra

# arl_pct es nullable en `parametros_legales` (varía por clase de riesgo [DEFINIR contador]); el snapshot
# lo necesita presente. Si la fila vigente no lo trae, se congela 0 (provisional, sin producción DIAN aún).
_CERO = Decimal("0")


class SqlNominaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- parámetros legales ---------------------------------------------------
    async def parametros_vigentes(self, fecha: date) -> ParametrosLegales | None:
        """Fila de `parametros_legales` cuya vigencia cubre `fecha` (la más reciente que aplique).

        Vigente = `vigente_desde <= fecha AND (vigente_hasta IS NULL OR vigente_hasta >= fecha)`.
        """
        stmt = (
            select(ParametrosLegales)
            .where(
                ParametrosLegales.vigente_desde <= fecha,
                (ParametrosLegales.vigente_hasta.is_(None))
                | (ParametrosLegales.vigente_hasta >= fecha),
            )
            .order_by(ParametrosLegales.vigente_desde.desc())
            .limit(1)
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    # --- periodos -------------------------------------------------------------
    async def crear_periodo(
        self, datos: PeriodoCrear, params: ParametrosLegales
    ) -> PeriodoNomina:
        """Crea el periodo congelando el snapshot de `params` (columnas `param_*`). Solo flush."""
        periodo = PeriodoNomina(
            nombre=datos.nombre,
            tipo=datos.tipo,
            fecha_inicio=datos.fecha_inicio,
            fecha_fin=datos.fecha_fin,
            estado="ABIERTO",
            parametros_legales_id=params.id,
            param_smmlv=params.smmlv,
            param_auxilio_transporte=params.auxilio_transporte,
            param_auxilio_transporte_tope_smmlv=params.auxilio_transporte_tope_smmlv,
            param_horas_mes=params.horas_mes,
            param_recargo_he_diurna=params.recargo_he_diurna,
            param_recargo_he_nocturna=params.recargo_he_nocturna,
            param_recargo_dominical=params.recargo_dominical,
            param_salud_empleado_pct=params.salud_empleado_pct,
            param_pension_empleado_pct=params.pension_empleado_pct,
            param_salud_empleador_pct=params.salud_empleador_pct,
            param_pension_empleador_pct=params.pension_empleador_pct,
            param_arl_pct=params.arl_pct if params.arl_pct is not None else _CERO,
            param_caja_compensacion_pct=params.caja_compensacion_pct,
            param_sena_pct=params.sena_pct,
            param_icbf_pct=params.icbf_pct,
            param_cesantias_pct=params.cesantias_pct,
            param_intereses_cesantias_pct=params.intereses_cesantias_pct,
            param_prima_pct=params.prima_pct,
            param_vacaciones_pct=params.vacaciones_pct,
        )
        self._s.add(periodo)
        await self._s.flush()  # asigna periodo.id
        return periodo

    async def obtener_periodo(self, periodo_id: int) -> PeriodoNomina | None:
        return await self._s.get(PeriodoNomina, periodo_id)

    async def listar_periodos(self) -> list[PeriodoNomina]:
        """Periodos, más recientes primero (por rango y por alta)."""
        stmt = select(PeriodoNomina).order_by(
            PeriodoNomina.fecha_inicio.desc(), PeriodoNomina.id.desc()
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def marcar_estado(
        self,
        periodo: PeriodoNomina,
        estado: str,
        *,
        ahora: datetime,
        liquidado_en: datetime | None = None,
        pagado_en: datetime | None = None,
    ) -> None:
        """Cambia el estado del periodo y sella el timestamp del hito. Solo flush."""
        periodo.estado = estado
        periodo.actualizado_en = ahora
        if liquidado_en is not None:
            periodo.liquidado_en = liquidado_en
        if pagado_en is not None:
            periodo.pagado_en = pagado_en
        await self._s.flush()

    # --- trabajadores / asistencia (import read-only de otros módulos) --------
    async def trabajadores_activos(self) -> list[Trabajador]:
        """Trabajadores vigentes y activos (candidatos a liquidar), por apellidos/nombres."""
        stmt = (
            select(Trabajador)
            .where(Trabajador.eliminado_en.is_(None), Trabajador.activo.is_(True))
            .order_by(Trabajador.apellidos, Trabajador.nombres)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def asistencia_de(
        self, trabajador_id: int, desde: date, hasta: date
    ) -> list[RegistroAsistencia]:
        """Registros de asistencia de un trabajador dentro del rango [desde, hasta] (inclusive)."""
        stmt = (
            select(RegistroAsistencia)
            .where(
                RegistroAsistencia.trabajador_id == trabajador_id,
                RegistroAsistencia.fecha >= desde,
                RegistroAsistencia.fecha <= hasta,
            )
            .order_by(RegistroAsistencia.fecha)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def registrar_asistencia(self, datos: AsistenciaCrear, fecha: date) -> RegistroAsistencia:
        """Inserta un registro de asistencia (la `fecha` ya viene resuelta a hoy Colombia si faltaba)."""
        registro = RegistroAsistencia(
            trabajador_id=datos.trabajador_id,
            fecha=fecha,
            obra_id=datos.obra_id,
            horas_trabajadas=datos.horas_trabajadas,
            horas_extra_diurnas=datos.horas_extra_diurnas,
            horas_extra_nocturnas=datos.horas_extra_nocturnas,
            horas_dominical_festivo=datos.horas_dominical_festivo,
            ausencia=datos.ausencia,
            observaciones=datos.observaciones,
            origen_registro="MANUAL",
        )
        self._s.add(registro)
        await self._s.flush()  # asigna registro.id
        return registro

    # --- detalles de liquidación (UPSERT idempotente) -------------------------
    async def upsert_detalle(
        self,
        periodo_id: int,
        trabajador_id: int,
        *,
        tipo_vinculacion: str,
        dias: Decimal,
        liq: Liquidacion,
        ahora: datetime,
    ) -> DetalleLiquidacion:
        """Inserta o actualiza el detalle del trabajador (UNIQUE por periodo+trabajador). Solo flush.

        ATÓMICO — `INSERT ... ON CONFLICT (periodo_id, trabajador_id) DO UPDATE` (upsert de Postgres):
        dos liquidaciones concurrentes del mismo periodo/trabajador NO colisionan (antes, el
        SELECT-then-INSERT dejaba a la perdedora con un 500 por la UNIQUE). Re-liquidar sobreescribe los
        montos con la nueva corrida pero NO toca `cune_dian`/`fecha_transmision_dian` (nómina
        electrónica, Fase 7): quedan fuera del `SET` del conflicto, así un detalle ya transmitido
        conserva su CUNE.
        """
        valores = {
            "periodo_id": periodo_id,
            "trabajador_id": trabajador_id,
            "tipo_vinculacion": tipo_vinculacion,
            "dias_liquidados": dias,
            "salario_devengado": liq.salario_devengado,
            "auxilio_transporte": liq.auxilio_transporte,
            "valor_horas_extra": liq.valor_horas_extra,
            "total_devengado": liq.total_devengado,
            "salud_empleado": liq.salud_empleado,
            "pension_empleado": liq.pension_empleado,
            "total_deducciones": liq.total_deducciones,
            "neto_pagar": liq.neto_pagar,
            "aportes_empleador": liq.aportes_empleador,
            "provisiones": liq.provisiones,
            "actualizado_en": ahora,
        }
        stmt = pg_insert(DetalleLiquidacion).values(**valores)
        # En el UPDATE del conflicto se pisan todas las columnas EXCEPTO las llaves y las de DIAN
        # (cune_dian/fecha_transmision_dian, ausentes de `valores`): un detalle transmitido conserva su CUNE.
        set_ = {
            col: getattr(stmt.excluded, col)
            for col in valores
            if col not in ("periodo_id", "trabajador_id")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["periodo_id", "trabajador_id"], set_=set_
        ).returning(DetalleLiquidacion.id)
        detalle_id = (await self._s.execute(stmt)).scalar_one()
        await self._s.flush()
        # `populate_existing` refresca la instancia mapeada con los valores ya persistidos (evita
        # devolver una copia obsoleta si el detalle estuviera en el identity map de la sesión).
        return (
            await self._s.execute(
                select(DetalleLiquidacion)
                .where(DetalleLiquidacion.id == detalle_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def reemplazar_prorrateos(
        self,
        periodo_id: int,
        trabajador_id: int,
        prorrateos: list[ProrrateoObra],
        *,
        ahora: datetime,
    ) -> int:
        """Borra los prorrateos previos del (periodo, trabajador) e inserta los nuevos. Solo flush.

        El borrado+reinserción hace la operación idempotente: re-liquidar no acumula filas duplicadas.
        Devuelve cuántas filas quedaron.
        """
        await self._s.execute(
            ProrrateoNominaObra.__table__.delete().where(
                (ProrrateoNominaObra.periodo_id == periodo_id)
                & (ProrrateoNominaObra.trabajador_id == trabajador_id)
            )
        )
        for pr in prorrateos:
            self._s.add(
                ProrrateoNominaObra(
                    periodo_id=periodo_id,
                    trabajador_id=trabajador_id,
                    obra_id=int(pr.obra_id) if pr.obra_id is not None else None,
                    dias_imputados=pr.dias_imputados,
                    costo_imputado=pr.costo_imputado,
                    creado_en=ahora,
                )
            )
        await self._s.flush()
        return len(prorrateos)

    async def eliminar_liquidaciones_ausentes(
        self, periodo_id: int, trabajador_ids_liquidados: list[int]
    ) -> int:
        """Purga detalle + prorrateo del periodo de los trabajadores que YA NO liquidan (reemplazo
        ATÓMICO del set liquidado). Sin esto, al re-liquidar, quien queda con 0 días hace `continue` en
        el servicio y sus filas VIEJAS persisten inflando los totales del periodo y el costo de obra.

        Preserva los detalles ya transmitidos a DIAN (`cune_dian` no nulo, Fase 7): un documento fiscal
        no se borra en una re-liquidación. Devuelve cuántos detalles se purgaron. Solo flush.
        """
        liquidados = set(trabajador_ids_liquidados)
        filas = (
            await self._s.execute(
                select(DetalleLiquidacion.trabajador_id, DetalleLiquidacion.cune_dian).where(
                    DetalleLiquidacion.periodo_id == periodo_id
                )
            )
        ).all()
        ausentes = [tid for tid, cune in filas if tid not in liquidados and cune is None]
        if not ausentes:
            return 0
        await self._s.execute(
            ProrrateoNominaObra.__table__.delete().where(
                (ProrrateoNominaObra.periodo_id == periodo_id)
                & (ProrrateoNominaObra.trabajador_id.in_(ausentes))
            )
        )
        await self._s.execute(
            DetalleLiquidacion.__table__.delete().where(
                (DetalleLiquidacion.periodo_id == periodo_id)
                & (DetalleLiquidacion.trabajador_id.in_(ausentes))
            )
        )
        await self._s.flush()
        return len(ausentes)

    # --- lecturas para el router ----------------------------------------------
    async def detalles_de(self, periodo_id: int) -> list[DetalleLiquidacion]:
        stmt = (
            select(DetalleLiquidacion)
            .where(DetalleLiquidacion.periodo_id == periodo_id)
            .order_by(DetalleLiquidacion.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def detalle_de(
        self, periodo_id: int, trabajador_id: int
    ) -> DetalleLiquidacion | None:
        return (
            await self._s.execute(
                select(DetalleLiquidacion).where(
                    DetalleLiquidacion.periodo_id == periodo_id,
                    DetalleLiquidacion.trabajador_id == trabajador_id,
                )
            )
        ).scalar_one_or_none()

    async def prorrateos_de_trabajador(
        self, periodo_id: int, trabajador_id: int
    ) -> list[ProrrateoNominaObra]:
        stmt = (
            select(ProrrateoNominaObra)
            .where(
                ProrrateoNominaObra.periodo_id == periodo_id,
                ProrrateoNominaObra.trabajador_id == trabajador_id,
            )
            .order_by(ProrrateoNominaObra.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def trabajadores_map(self, ids: list[int]) -> dict[int, Trabajador]:
        """`{id: Trabajador}` para resolver nombre/documento en las lecturas (sin N+1)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(select(Trabajador).where(Trabajador.id.in_(ids)))
        ).scalars().all()
        return {t.id: t for t in filas}

    async def obras_nombres(self, ids: list[int]) -> dict[int, str]:
        """`{obra_id: nombre}` para etiquetar el prorrateo por obra (sin N+1)."""
        if not ids:
            return {}
        filas = (
            await self._s.execute(select(Obra.id, Obra.nombre).where(Obra.id.in_(ids)))
        ).all()
        return {oid: nombre for oid, nombre in filas}
