"""Modelo del pack FAQ / conocimiento (fuente: migración 0012_faq_conocimiento).

`Conocimiento` es una entrada de conocimiento del negocio (un tema: ubicación, horarios, precios,
formas de pago, parqueo, políticas…). El negocio la nutre desde el dashboard; el agente la consulta
con `responder_faq`. Vive en la base del propio tenant (aislamiento por construcción, sin `empresa_id`).
Fechas en TIMESTAMPTZ (se operan en hora Colombia, `COLOMBIA_TZ`, regla no negociable #4).
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class Conocimiento(TenantBase):
    """Una entrada de conocimiento del negocio: `titulo` + `contenido`, con `activo` y `orden`."""

    __tablename__ = "conocimiento"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    orden: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
