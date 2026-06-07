"""Modelos del control DB necesarios para resolver la empresa.

Las tablas las crea migrations/control; aquí solo el mapeo para el repositorio de control.
"""
from sqlalchemy import BigInteger, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import ControlBase


class Empresa(ControlBase):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    nit: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    estado: Mapped[str] = mapped_column(String, nullable=False)


class TenantDatabase(ControlBase):
    __tablename__ = "tenant_databases"

    empresa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("empresas.id"), primary_key=True)
    db_name: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(Text, nullable=False)
    connection_url_cifrada: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class WaNumero(ControlBase):
    """Mapeo del número/canal de WhatsApp (Kapso) → empresa. Resuelve el tenant por `phone_number_id`.

    Un webhook único de Kapso atiende a todos los tenants; cada payload trae el `phone_number_id`
    del número que recibió el mensaje, y esta tabla dice a qué empresa pertenece. No hay secretos
    aquí: las credenciales de Kapso son de plataforma (env). `numero`/`waba_id` son de referencia.
    """

    __tablename__ = "wa_numeros"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone_number_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("empresas.id"), nullable=False)
    waba_id: Mapped[str | None] = mapped_column(Text)
    numero: Mapped[str | None] = mapped_column(Text)        # número legible (+57…), referencia
    estado: Mapped[str] = mapped_column(Text, nullable=False)  # activo | inactivo
