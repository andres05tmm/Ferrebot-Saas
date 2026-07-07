"""Parámetros legales de nómina (vertical construcción, spec cliente 01/08 — tenant 0043).

`parametros_legales` vive aquí (y no en `modules.trabajadores`) porque no es un dato del trabajador
sino la tabla de configuración que alimenta el MOTOR de nómina (Fase 4 del plan PIM: liquidación de
directos/patacalientes, aportes y provisiones). El repo agrupa por concern de dominio (caja, fiados,
cobranza…), y la nómina es su propio concern; el motor congelará un snapshot de estos parámetros al
crear cada `PeriodoNomina`. Es tabla de NEGOCIO del tenant (sin `empresa_id`: la base ES la frontera).

Vigencia por fecha: `vigente_desde`/`vigente_hasta` (NULL = fila vigente actual); no hay soft delete
`eliminado_en` porque el cierre de una parametrización se modela cerrando su vigencia, no borrándola.
Dinero (SMMLV, auxilio) en MONEY4 (18,4); los porcentajes en NUMERIC(6,4) — fracciones 0–1 con la
misma precisión de 4 decimales que trae la spec (0.0833, 0.0417). Valores [DEFINIR con contador]
marcados en cada columna: se siembran provisionales y se corrigen al recibir los reales (Fase 4).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# Porcentaje como fracción (0.04 = 4%). 4 decimales: es la precisión de la spec (0.0833, 0.0417).
PORCENTAJE = Numeric(6, 4)


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
