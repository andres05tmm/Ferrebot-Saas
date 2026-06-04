"""Bases declarativas separadas: plano de control vs plano de negocio (tenant).

No mezclar modelos de ambos planos (regla de multitenancy #3). Las migraciones son DDL
a mano (materializan schema.md exactamente); estos modelos mapean esas tablas para los
repositorios.
"""
from sqlalchemy.orm import DeclarativeBase


class ControlBase(DeclarativeBase):
    """Modelos del control DB (empresas, tenant_databases, ...)."""


class TenantBase(DeclarativeBase):
    """Modelos del esquema de negocio por empresa (productos, ventas, ...). Sin empresa_id."""
