"""Modelo Cliente (schema.md / tenant 0001, extendido por el vertical construcción en 0046).

`saldo_fiado` es un contador denormalizado del saldo de crédito; la fuente de verdad es
`fiados_movimientos` (se actualiza en la misma transacción que el movimiento).

El vertical construcción (spec cliente 02) suma un mini-CRM: `estatus` (PROSPECTO→MOROSO), datos de
`contacto_*` y un `acuerdo_comercial` de texto libre. Son columnas NULLABLE agregadas al final por la
migración 0046 (backward-compatible: el POS no las requiere); el enum `estatus_cliente` lo crea esa
migración (create_type=False). `estatus` trae server_default 'PROSPECTO' (default de la spec).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

# El tipo lo crea la migración 0046 (create_type=False): aquí solo se mapea. Literales EXACTOS a la spec.
estatus_cliente = PgEnum(
    "PROSPECTO", "ACTIVO", "RECURRENTE", "INACTIVO", "MOROSO",
    name="estatus_cliente", create_type=False,
)


class Cliente(TenantBase):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_documento: Mapped[str | None] = mapped_column(Text)
    documento: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(Text)
    correo: Mapped[str | None] = mapped_column(Text)
    direccion: Mapped[str | None] = mapped_column(Text)
    ciudad_dane: Mapped[str | None] = mapped_column(Text)
    regimen: Mapped[str | None] = mapped_column(Text)
    saldo_fiado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- Vertical construcción (mini-CRM, spec 02 / tenant 0046). Columnas nullable. ---
    estatus: Mapped[str | None] = mapped_column(estatus_cliente, server_default="PROSPECTO")
    contacto_nombre: Mapped[str | None] = mapped_column(Text)
    contacto_cargo: Mapped[str | None] = mapped_column(Text)
    contacto_telefono: Mapped[str | None] = mapped_column(Text)
    contacto_email: Mapped[str | None] = mapped_column(Text)
    acuerdo_comercial: Mapped[str | None] = mapped_column(Text)   # condiciones de pago/descuentos
