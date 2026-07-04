"""Los 7 modelos ORM antes huérfanos (ADR 0025) viven en el plano TENANT, no en el control.

Invariante de aislamiento: las tablas de negocio se mapean sobre `TenantBase.metadata` (la base ES la
frontera del tenant), nunca sobre `ControlBase`. Además cada modelo debe existir como tabla real en el
esquema tenant migrado a head (persistencia + reconciliación 0030 alineada con la metadata).
"""
from sqlalchemy import inspect

from core.db.base import ControlBase, TenantBase
from modules.bancos.models import BancolombiaTransferencia
from modules.cobranza.models import CuentaCobro
from modules.facturacion.models import DocumentoSoporte, EventoDian, NotaElectronica
from modules.reportes.models import IvaSaldoBimestral, LibroIVA

_MODELOS = [
    NotaElectronica, DocumentoSoporte, EventoDian,
    IvaSaldoBimestral, LibroIVA, CuentaCobro, BancolombiaTransferencia,
]


def test_modelos_estan_en_metadata_tenant_no_control():
    control = set(ControlBase.metadata.tables)
    for modelo in _MODELOS:
        assert modelo.__table__.metadata is TenantBase.metadata, modelo.__name__
        assert modelo.__tablename__ in TenantBase.metadata.tables, modelo.__name__
        assert modelo.__tablename__ not in control, modelo.__name__


async def test_cada_tabla_existe_en_el_esquema_tenant(tenant):
    async with tenant.engine.connect() as conn:
        reales = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    for modelo in _MODELOS:
        assert modelo.__tablename__ in reales, modelo.__tablename__
