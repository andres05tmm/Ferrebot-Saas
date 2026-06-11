"""Modelos del pack postventa (plan §2.6 / tenant 0023)."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class PostventaConfig(TenantBase):
    __tablename__ = "postventa_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    horas_tras_evento: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    seguir_citas: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    seguir_pedidos: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    google_maps_url: Mapped[str | None] = mapped_column(Text)
    calificacion_minima_resena: Mapped[int] = mapped_column(Integer, nullable=False, default=4)


class PostventaEnvio(TenantBase):
    """Log/dedup append-only: un seguimiento por (origen, origen_id) — jamás se repite."""

    __tablename__ = "postventa_envios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    origen: Mapped[str] = mapped_column(Text, nullable=False)        # cita | pedido
    origen_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telefono: Mapped[str] = mapped_column(Text, nullable=False)
    enviado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EncuestaRespuesta(TenantBase):
    __tablename__ = "encuestas_respuestas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telefono: Mapped[str] = mapped_column(Text, nullable=False)
    calificacion: Mapped[int] = mapped_column(Integer, nullable=False)
    comentario: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
