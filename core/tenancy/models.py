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
