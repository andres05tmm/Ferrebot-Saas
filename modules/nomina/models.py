"""Nómina del vertical construcción: parámetros legales + periodos, liquidación y prorrateo a obra
(spec cliente 01/08 — tenants 0043/0047).

`parametros_legales` vive aquí (y no en `modules.trabajadores`) porque no es un dato del trabajador
sino la tabla de configuración que alimenta el MOTOR de nómina (Fase 4 del plan PIM: liquidación de
directos/patacalientes, aportes y provisiones). El repo agrupa por concern de dominio (caja, fiados,
cobranza…), y la nómina es su propio concern; el motor congela un snapshot de estos parámetros al
crear cada `PeriodoNomina` (columnas `param_*` de esa tabla). Son tablas de NEGOCIO del tenant (sin
`empresa_id`: la base ES la frontera). Los tipos enum los crean las migraciones 0043/0047
(create_type=False): aquí solo se mapean, con literales EXACTOS.

Vigencia por fecha: `vigente_desde`/`vigente_hasta` (NULL = fila vigente actual); no hay soft delete
`eliminado_en` porque el cierre de una parametrización se modela cerrando su vigencia, no borrándola.
Dinero (SMMLV, auxilio) en MONEY4 (18,4); los porcentajes en NUMERIC(6,4) — fracciones 0–1 con la
misma precisión de 4 decimales que trae la spec (0.0833, 0.0417). Valores [DEFINIR con contador]
marcados en cada columna: se siembran provisionales y se corrigen al recibir los reales (Fase 4).

Patrón del repo (ver `modules.obra`/`modules.maquinaria`): las FKs viven en la MIGRACIÓN; el ORM mapea
los ids como BigInteger sin `relationship`. La liquidación (`DetalleLiquidacion`) y el prorrateo
(`ProrrateoNominaObra`) espejan los dataclass puros de `services.calculations.nomina` (una fórmula, una
verdad): el motor calcula, estos modelos persisten el resultado ya cuantizado.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Integer, Numeric, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# Porcentaje/recargo como fracción o multiplicador (0.04 = 4%; 1.25 = recargo de HE diurna). 4 decimales:
# es la precisión de la spec (0.0833, 0.0417).
PORCENTAJE = Numeric(6, 4)
HORAS_MES_T = Numeric(6, 2)   # convención de horas/mes (240.00)
CANTIDAD = Numeric(18, 4)     # días/horas imputadas (misma precisión que la spec)

# Enums de nómina (0047). `tipo_vinculacion` es dueño 0043; se referencia en `DetalleLiquidacion`.
estado_periodo_nomina = PgEnum(
    "ABIERTO", "LIQUIDADO", "PAGADO", name="estado_periodo_nomina", create_type=False
)
tipo_periodo_nomina = PgEnum(
    "QUINCENAL", "MENSUAL", "SEMANAL", name="tipo_periodo_nomina", create_type=False
)
tipo_vinculacion = PgEnum(
    "DIRECTO", "PATACALIENTE", name="tipo_vinculacion", create_type=False
)
# Máquina de estados de la transmisión a DIAN de la nómina electrónica (Fase 7, migración 0050). Espeja
# el operativo de `fe_estado`: TRANSMITIDO≈aceptada (idempotencia dura: no re-transmitir), RECHAZADO≈
# rechazada (terminal de negocio), ERROR≈error (transitorio/5xx, reintentable). El tipo lo crea 0050
# (create_type=False): aquí solo se mapea, con literales EXACTOS.
estado_transmision_nomina = PgEnum(
    "PENDIENTE", "TRANSMITIDO", "RECHAZADO", "ERROR",
    name="estado_transmision_nomina", create_type=False,
)


class ParametrosLegales(TenantBase):
    """Set de parámetros legales vigente por rango de fechas (spec `ParametrosLegales`)."""

    __tablename__ = "parametros_legales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vigente_desde: Mapped[date] = mapped_column(Date, nullable=False)   # ej. 2026-01-01
    vigente_hasta: Mapped[date | None] = mapped_column(Date)            # NULL = vigente actual

    # Base salarial (dinero → MONEY4). 2026: SMMLV 1.750.905, auxilio transporte 249.095.
    smmlv: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    auxilio_transporte: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # El auxilio de transporte solo aplica hasta N SMMLV de salario (regla legal, hoy 2).
    auxilio_transporte_tope_smmlv: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )

    # Horas extra (agregadas por 0047 para el snapshot del motor): convención de horas/mes y recargos
    # como MULTIPLICADOR (1.25 diurna, 1.75 nocturna, 2.0 dominical). NOT NULL con default PROVISIONAL
    # [DEFINIR con contador]: la mecánica está fija; los valores reales se ajustan al recibirlos.
    horas_mes: Mapped[Decimal] = mapped_column(
        HORAS_MES_T, nullable=False, server_default="240"
    )
    recargo_he_diurna: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="1.25"
    )
    recargo_he_nocturna: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="1.75"
    )
    recargo_dominical: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="2.0"
    )

    # Deducciones del empleado (aportes a su cargo): salud y pensión 4% cada una.
    salud_empleado_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    pension_empleado_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    # Aportes del empleador (para el costeo real de obra, no salen del sueldo). [DEFINIR con contador].
    salud_empleador_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    pension_empleador_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    arl_pct: Mapped[Decimal | None] = mapped_column(PORCENTAJE)  # varía por clase de riesgo [DEFINIR]

    # Parafiscales (defaults legales estándar).
    caja_compensacion_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0.04"
    )
    sena_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0.02")
    icbf_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0.03")

    # Provisiones prestacionales (cesantías, intereses, prima, vacaciones).
    cesantias_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0.0833"
    )
    intereses_cesantias_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0.01"
    )
    prima_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0.0833")
    vacaciones_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0.0417"
    )

    iva_general: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0.19")
    notas: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PeriodoNomina(TenantBase):
    """Periodo de liquidación con snapshot congelado de parámetros (spec 08 `PeriodoNomina`).

    Ciclo de vida (estado): ABIERTO (se puede liquidar/re-liquidar) → LIQUIDADO (cerrado, no admite más
    edición) → PAGADO. Al CREARSE congela un snapshot de la fila vigente de `parametros_legales` en las
    columnas `param_*`: la liquidación del periodo usa esos valores aunque después cambie la
    parametrización (invariante de la spec, "freeze the valid ParametrosLegales row"). Los `param_*`
    espejan `services.calculations.nomina.ParametrosNomina` (mapeo trivial en el repo/service).
    """

    __tablename__ = "periodos_nomina"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str | None] = mapped_column(Text)   # etiqueta libre ("Quincena 1 jul 2026")
    tipo: Mapped[str] = mapped_column(tipo_periodo_nomina, nullable=False, server_default="QUINCENAL")
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(
        estado_periodo_nomina, nullable=False, server_default="ABIERTO"
    )
    # Traza a la fila usada (la FK va en la migración; SET NULL si se borra). El valor congelado vive en
    # los param_*, así que no depende de esta referencia.
    parametros_legales_id: Mapped[int | None] = mapped_column(BigInteger)

    # Snapshot congelado (todas las columnas del ParametrosNomina que consume el motor).
    param_smmlv: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    param_auxilio_transporte: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    param_auxilio_transporte_tope_smmlv: Mapped[int] = mapped_column(Integer, nullable=False)
    param_horas_mes: Mapped[Decimal] = mapped_column(HORAS_MES_T, nullable=False)
    param_recargo_he_diurna: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_recargo_he_nocturna: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_recargo_dominical: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_salud_empleado_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_pension_empleado_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_salud_empleador_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_pension_empleador_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_arl_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_caja_compensacion_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_sena_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_icbf_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_cesantias_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_intereses_cesantias_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_prima_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)
    param_vacaciones_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False)

    liquidado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pagado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DetalleLiquidacion(TenantBase):
    """Liquidación de un trabajador en un periodo (espeja `services.calculations.nomina.Liquidacion`).

    UNIQUE(periodo_id, trabajador_id) en la base = un detalle por trabajador; ancla la idempotencia de
    re-liquidar (UPSERT en el repo). `cune_dian`/`fecha_transmision_dian` son de la nómina electrónica
    (Fase 7): en la Ola A quedan NULL y NO se tocan al re-liquidar. Los montos ya vienen cuantizados del
    motor (redondeo solo al final, skill money-safe).
    """

    __tablename__ = "detalles_liquidacion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    periodo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trabajador_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo_vinculacion: Mapped[str] = mapped_column(tipo_vinculacion, nullable=False)
    dias_liquidados: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False, server_default="0")

    # Devengados.
    salario_devengado: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    auxilio_transporte: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    valor_horas_extra: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    total_devengado: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # Deducciones (empleado).
    salud_empleado: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    pension_empleado: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    total_deducciones: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # Neto a pagar.
    neto_pagar: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # Aportes empleador + provisiones (costeo real, no van al neto).
    aportes_empleador: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    provisiones: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # Nómina electrónica (Fase 7, transmisión a DIAN). `cune_dian`/`fecha_transmision_dian` (de 0047) dan
    # el ancla mínima de idempotencia (NULL=no transmitido, set=transmitido); la migración 0050 completa
    # la máquina de estados espejo de `fe_estado` para reintentos/observabilidad: `estado_transmision`
    # (PENDIENTE→TRANSMITIDO|RECHAZADO|ERROR; una vez TRANSMITIDO no se re-transmite), `intentos_transmision`
    # (acota el dead-letter del pipeline ARQ) y `transmision_respuesta` (respuesta MATIAS completa: motivo
    # de rechazo + histórico fiscal). Transmisión SOLO de directos (spec 08); el patacaliente queda
    # PENDIENTE pero el pipeline lo excluye filtrando `tipo_vinculacion='DIRECTO'`. En la Ola A todo esto
    # queda en su default (PENDIENTE / 0 / NULL): nada transmitido.
    cune_dian: Mapped[str | None] = mapped_column(Text)
    fecha_transmision_dian: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado_transmision: Mapped[str] = mapped_column(
        estado_transmision_nomina, nullable=False, server_default="PENDIENTE"
    )
    intentos_transmision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    transmision_respuesta: Mapped[dict | None] = mapped_column(JSONB)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProrrateoNominaObra(TenantBase):
    """Porción del costo total de un trabajador imputada a una obra (o a admin) — spec 08 §3.

    Espeja `services.calculations.nomina.ProrrateoObra`. `obra_id = NULL` es nómina administrativa (días
    no imputables a una obra). Alimenta el gasto real de obra (Fase 3), por eso el índice por `obra_id`.
    INVARIANTE: la suma de `costo_imputado` de un trabajador en el periodo ≡ su costo total liquidado
    (garantizado por la función pura de reparto por mayor resto; se verifica end-to-end en los tests).
    """

    __tablename__ = "prorrateo_nomina_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    periodo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trabajador_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obra_id: Mapped[int | None] = mapped_column(BigInteger)   # NULL = administrativo
    dias_imputados: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    costo_imputado: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
