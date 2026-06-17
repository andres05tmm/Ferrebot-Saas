"""Modelos del pack pagar (ADR 0019 / tenant 0026).

El saldo NO vive aquí: la fuente de verdad de las cuentas por pagar sigue siendo
`facturas_proveedores` (`pendiente` lo recalcula el flujo de abonos de `modules/proveedores`; este
pack solo lo LEE). Aquí vive el plano de avisos: la config del negocio y el estado de dedup por
factura (cuántas veces se avisó de ESA factura y cuándo) para no repetir el mismo aviso al dueño.

A diferencia de `pack_cobranza`, el aviso es INTERNO al dueño: no hay opt-out, ni promesas, ni
plano de cara a un tercero.
"""
from datetime import datetime, time

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class PagarConfig(TenantBase):
    __tablename__ = "pagar_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Avisar N días ANTES de vencer (0 = solo al vencer / vencidas).
    dias_aviso_previo: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # No repetir el aviso de la misma factura antes de N días.
    cadencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 0))
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False, default=time(18, 0))
    # Vencimiento derivado cuando `facturas_proveedores.fecha_vencimiento` es NULL.
    plazo_default_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=30)


class PagarAviso(TenantBase):
    """Estado de dedup/cadencia por factura: el motor sella aquí SOLO tras un aviso exitoso.

    `factura_id` es la PK natural de `facturas_proveedores` (nº de factura del proveedor, TEXT). El
    FK con CASCADE limpia el estado si la factura se borra.
    """

    __tablename__ = "pagar_avisos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    factura_id: Mapped[str] = mapped_column(
        Text, ForeignKey("facturas_proveedores.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    avisos_enviados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ultimo_aviso_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
