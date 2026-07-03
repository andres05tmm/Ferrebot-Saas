"""Modelos del pack cobranza (ADR 0015 / tenant 0017).

El saldo NO vive aquí: la fuente de verdad sigue siendo `fiados_movimientos` y el contador
`clientes.saldo_fiado` (este pack solo los LEE). Aquí vive el plano de cobranza: la config del
negocio, el estado de recordatorios por cliente (opt-out, tope, dedup), las promesas de pago y los
pagos reportados por verificar.
"""
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, Text, Time, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)


class CuentaCobro(TenantBase):
    """Cuenta de cobro (honorarios/servicios) — tenant 0001, mapeada por ADR 0025.

    Documento de cobro previo al documento soporte DIAN (`documentos_soporte.cuenta_cobro_id`).
    `cliente_id` referencia `clientes.id` (FK en la base; columna plana en el ORM para no acoplar
    el grafo de mappers entre módulos, mismo criterio que el resto de tablas huérfanas).
    """

    __tablename__ = "cuentas_cobro"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consecutivo: Mapped[int | None] = mapped_column(BigInteger)
    numero_display: Mapped[str | None] = mapped_column(Text)
    periodo: Mapped[str | None] = mapped_column(Text)
    concepto: Mapped[str | None] = mapped_column(Text)
    valor: Mapped[Decimal | None] = mapped_column(MONEY)
    cliente_id: Mapped[int | None] = mapped_column(BigInteger)
    enviado_telegram: Mapped[bool | None] = mapped_column(Boolean, default=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

promesa_estado = PgEnum(
    "vigente", "cumplida", "incumplida", "reemplazada", name="promesa_estado", create_type=False
)


class CobranzaConfig(TenantBase):
    __tablename__ = "cobranza_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cadencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    max_recordatorios: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 0))
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False, default=time(19, 0))
    saldo_minimo: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))


class CobranzaCliente(TenantBase):
    __tablename__ = "cobranza_clientes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recordatorios_enviados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ultimo_recordatorio_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CobranzaRecordatorio(TenantBase):
    """Log append-only: una fila por recordatorio ENVIADO (base de la métrica "pesos recuperados").

    A diferencia de `cobranza_clientes` (estado vivo, se resetea al cerrar el ciclo), este log
    nunca se borra: permite atribuir abonos posteriores a la gestión del agente.
    """

    __tablename__ = "cobranza_recordatorios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telefono: Mapped[str] = mapped_column(Text, nullable=False)
    saldo: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    enviado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PromesaPago(TenantBase):
    __tablename__ = "promesas_pago"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telefono: Mapped[str] = mapped_column(Text, nullable=False)
    fecha_promesa: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(promesa_estado, nullable=False, default="vigente")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PagoReportado(TenantBase):
    __tablename__ = "pagos_reportados"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telefono: Mapped[str] = mapped_column(Text, nullable=False)
    nota: Mapped[str | None] = mapped_column(Text)
    verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
