"""Modelo ORM de movimientos bancarios (`bancolombia_transferencias`, tenant 0001 + 0035).

Tabla de negocio sin `empresa_id`: la base ES la frontera del tenant. Nació (0001) como bitácora de
transferencias ENTRANTES parseadas de Gmail (`gmail_message_id` UNIQUE = idempotencia de ESE canal).
La conciliación bancaria (ADR 0028 / tenant 0035) la adopta como el libro de movimientos bancarios:
`referencia_bancaria` (UNIQUE parcial) es la idempotencia de la ingesta del extracto, `naturaleza`
separa créditos de débitos, y `estado_conciliacion` + el enlace (`conciliado_con_*`) llevan el ciclo
no_conciliado → sugerido → conciliado SIN tocar ningún saldo.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

conciliacion_estado = PgEnum(
    "no_conciliado", "sugerido", "conciliado",
    name="conciliacion_estado", create_type=False,
)


class BancolombiaTransferencia(TenantBase):
    __tablename__ = "bancolombia_transferencias"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Nullable desde 0035: la ingesta de un extracto no viene de Gmail (la UNIQUE admite múltiples NULL).
    gmail_message_id: Mapped[str | None] = mapped_column(Text, unique=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    hora: Mapped[str | None] = mapped_column(Text)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remitente: Mapped[str | None] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(Text)
    tipo_transaccion: Mapped[str | None] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(Text)
    notificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # --- conciliación bancaria (0035, ADR 0028) -------------------------------
    # Ancla de idempotencia de la ingesta del extracto (UNIQUE parcial WHERE NOT NULL).
    referencia_bancaria: Mapped[str | None] = mapped_column(Text)
    # 'credito' (entra plata: ventas por transferencia) | 'debito' (sale: gastos/abonos por banco).
    naturaleza: Mapped[str] = mapped_column(Text, nullable=False, default="credito")
    estado_conciliacion: Mapped[str] = mapped_column(
        conciliacion_estado, nullable=False, default="no_conciliado"
    )
    # Enlace polimórfico al movimiento interno (FK-less, como ventas→usuarios): tipo ∈ {venta,gasto,abono}.
    conciliado_con_tipo: Mapped[str | None] = mapped_column(Text)
    conciliado_con_id: Mapped[int | None] = mapped_column(BigInteger)
    conciliado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
