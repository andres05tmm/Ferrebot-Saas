"""Migración tenant 0042 — UNIQUE en `compras_fiscal.cufe_proveedor` (upgrade/downgrade limpios).

Corre contra una base efímera real (fixture `tenant`, ya en head). Verifica (ADR 0020 F1):
  - head trae el índice UNIQUE: dos compras fiscales con el MISMO CUFE violan la unicidad;
  - múltiples NULL conviven (las fiscales sin CUFE del Slice 6a no colisionan);
  - downgrade a 0041 lo revierte limpio; upgrade vuelve a head sin romper.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from tools._alembic import downgrade_tenant, upgrade_tenant

_INDICE = "uq_compras_fiscal_cufe_proveedor"
_EXISTE = "SELECT count(*) FROM pg_indexes WHERE indexname = :i"
_INSERT = (
    "INSERT INTO compras_fiscal (proveedor_nit, base, iva, total, cufe_proveedor, creado_en) "
    "VALUES ('900111', 0, 0, 1000, :cufe, :f)"
)
_CUFE = "c" * 96


async def test_0042_cufe_unico_y_nulls_conviven(tenant):
    # head (incluye 0042): el índice UNIQUE existe.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"i": _INDICE})).scalar_one() == 1

        # Dos fiscales SIN CUFE (NULL) conviven: la unicidad no bloquea el Slice 6a.
        await s.execute(text(_INSERT), {"cufe": None, "f": now_co()})
        await s.execute(text(_INSERT), {"cufe": None, "f": now_co()})
        # La primera con CUFE entra…
        await s.execute(text(_INSERT), {"cufe": _CUFE, "f": now_co()})
        await s.commit()

    # …y repetir el MISMO CUFE viola la unicidad (ancla de idempotencia de la recepción por QR).
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(IntegrityError):
            await s.execute(text(_INSERT), {"cufe": _CUFE, "f": now_co()})
            await s.commit()

    # downgrade a 0041 → el índice se va limpio.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0041_saldo_cache")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"i": _INDICE})).scalar_one() == 0

    # upgrade vuelve a head sin romper (el CUFE existente no choca: había solo uno).
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"i": _INDICE})).scalar_one() == 1
