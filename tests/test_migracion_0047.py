"""Migración 0047 (nómina): up/down/up limpio en base efímera (.claude/rules/testing.md).

Verifica que head trae las 3 tablas nuevas (`periodos_nomina`, `detalles_liquidacion`,
`prorrateo_nomina_obra`), sus 2 enums propios (`estado_periodo_nomina`, `tipo_periodo_nomina`) y las 4
columnas agregadas a `parametros_legales` (`horas_mes`, `recargo_he_diurna/nocturna`, `recargo_dominical`);
que el downgrade a 0046 las retira TODAS pero deja intacta `parametros_legales` (dueño 0043); y que
reaplica head limpio. `tipo_vinculacion` NO se dropea (dueño 0043): se reusa en `detalles_liquidacion`.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_TABLAS = ("periodos_nomina", "detalles_liquidacion", "prorrateo_nomina_obra")
_ENUMS = (
    "SELECT count(*) FROM pg_type WHERE typtype='e' "
    "AND typname IN ('estado_periodo_nomina','tipo_periodo_nomina')"
)
_COLS_PARAMS = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='parametros_legales' "
    "AND column_name IN ('horas_mes','recargo_he_diurna','recargo_he_nocturna','recargo_dominical')"
)


async def _existe_tabla(s: AsyncSession, tabla: str) -> bool:
    return (
        await s.execute(text(f"SELECT to_regclass('public.{tabla}') IS NOT NULL"))
    ).scalar_one()


async def test_0047_nomina_up_down_up(tenant):
    # head incluye 0047: las 3 tablas, los 2 enums y las 4 columnas nuevas existen.
    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS:
            assert await _existe_tabla(s, tabla) is True
        assert (await s.execute(text(_ENUMS))).scalar_one() == 2
        assert (await s.execute(text(_COLS_PARAMS))).scalar_one() == 4
        # El UNIQUE de idempotencia (un detalle por periodo+trabajador) existe.
        idx = (
            await s.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE indexname='uq_detalle_liquidacion_periodo_trabajador'"
                )
            )
        ).scalar_one()
        assert idx == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0046_ext_clientes_proveedores")

    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS:
            assert await _existe_tabla(s, tabla) is False
        assert (await s.execute(text(_ENUMS))).scalar_one() == 0        # los enums propios se fueron
        assert (await s.execute(text(_COLS_PARAMS))).scalar_one() == 0  # las 4 columnas se retiraron
        # parametros_legales y su enum reusado siguen ahí (dueño 0043).
        assert await _existe_tabla(s, "parametros_legales") is True
        tv = (
            await s.execute(
                text("SELECT count(*) FROM pg_type WHERE typtype='e' AND typname='tipo_vinculacion'")
            )
        ).scalar_one()
        assert tv == 1

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio (idempotente para el fixture)
    async with AsyncSession(tenant.engine) as s:
        assert await _existe_tabla(s, "periodos_nomina") is True
        assert (await s.execute(text(_COLS_PARAMS))).scalar_one() == 4
