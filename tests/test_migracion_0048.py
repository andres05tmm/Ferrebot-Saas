"""Migración 0048 (imputación a obra en gastos/compras + snapshot de liquidación): up/down/up limpio en
base efímera (.claude/rules/testing.md).

Verifica que head trae: (a) las 10 columnas nuevas de `gastos` con sus defaults (origen_registro=MANUAL,
requiere_revision=false) y la FK a obras; (b) las 6 columnas nuevas de `compras`; (c) la tabla
`liquidaciones_obra` con UNIQUE(obra_id); (d) los 4 enums propios (categoria_gasto, metodo_pago,
categoria_compra, semaforo_obra) y los índices en obra_id. Y que el downgrade a 0047 lo retira TODO
—columnas, tabla y los 4 enums propios— pero deja intacto `origen_registro` (dueño 0044, reusado en
gastos). Reaplica head limpio al final.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_COLS_GASTOS = (
    "obra_id", "maquina_id", "categoria_gasto", "metodo_pago", "numero_referencia",
    "comprobante_url", "origen_registro", "telegram_user_id", "telegram_message_id",
    "requiere_revision",
)
_COLS_COMPRAS = (
    "obra_id", "es_viaje_material", "precio_venta_cliente", "resbalo", "categoria", "factura_url",
)
_ENUMS = (
    "SELECT count(*) FROM pg_type WHERE typtype='e' "
    "AND typname IN ('categoria_gasto','metodo_pago_gasto','categoria_compra','semaforo_obra')"
)


async def _existe_tabla(s: AsyncSession, tabla: str) -> bool:
    return (
        await s.execute(text(f"SELECT to_regclass('public.{tabla}') IS NOT NULL"))
    ).scalar_one()


async def _cuenta_cols(s: AsyncSession, tabla: str, cols: tuple[str, ...]) -> int:
    return (
        await s.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name=:t AND column_name = ANY(:cols)"
            ),
            {"t": tabla, "cols": list(cols)},
        )
    ).scalar_one()


async def test_0048_up_down_up(tenant):
    # head incluye 0048: columnas nuevas, tabla, enums e índices existen.
    async with AsyncSession(tenant.engine) as s:
        assert await _cuenta_cols(s, "gastos", _COLS_GASTOS) == len(_COLS_GASTOS)
        assert await _cuenta_cols(s, "compras", _COLS_COMPRAS) == len(_COLS_COMPRAS)
        assert await _existe_tabla(s, "liquidaciones_obra") is True
        assert (await s.execute(text(_ENUMS))).scalar_one() == 4

        # Defaults de las 2 columnas NOT NULL de gastos (rellenan las filas del POS existentes).
        defaults = dict(
            (
                await s.execute(
                    text(
                        "SELECT column_name, column_default FROM information_schema.columns "
                        "WHERE table_name='gastos' "
                        "AND column_name IN ('origen_registro','requiere_revision')"
                    )
                )
            ).all()
        )
        assert "MANUAL" in defaults["origen_registro"]
        assert "false" in defaults["requiere_revision"]

        # UNIQUE(obra_id) en liquidaciones_obra = idempotencia de liquidar (una por obra).
        uq = (
            await s.execute(
                text(
                    "SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_name='liquidaciones_obra' AND constraint_type='UNIQUE'"
                )
            )
        ).scalar_one()
        assert uq == 1

        # FK gastos.obra_id → obras e índices en obra_id de ambas tablas.
        idx = (
            await s.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE indexname IN ('ix_gastos_obra_id','ix_compras_obra_id')"
                )
            )
        ).scalar_one()
        assert idx == 2
        fk = (
            await s.execute(
                text(
                    "SELECT count(*) FROM information_schema.key_column_usage k "
                    "JOIN information_schema.table_constraints c "
                    "  ON k.constraint_name=c.constraint_name "
                    "WHERE c.table_name='gastos' AND c.constraint_type='FOREIGN KEY' "
                    "AND k.column_name='obra_id'"
                )
            )
        ).scalar_one()
        assert fk == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0047_nomina_liquidacion")

    async with AsyncSession(tenant.engine) as s:
        assert await _cuenta_cols(s, "gastos", _COLS_GASTOS) == 0
        assert await _cuenta_cols(s, "compras", _COLS_COMPRAS) == 0
        assert await _existe_tabla(s, "liquidaciones_obra") is False
        assert (await s.execute(text(_ENUMS))).scalar_one() == 0   # los 4 enums propios se fueron
        # `origen_registro` (dueño 0044) sigue ahí: gastos lo reusaba, no se dropea.
        oreg = (
            await s.execute(
                text("SELECT count(*) FROM pg_type WHERE typtype='e' AND typname='origen_registro'")
            )
        ).scalar_one()
        assert oreg == 1
        # gastos/compras siguen existiendo (solo se quitaron las columnas del vertical).
        assert await _existe_tabla(s, "gastos") is True
        assert await _existe_tabla(s, "compras") is True

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio (idempotente para el fixture)
    async with AsyncSession(tenant.engine) as s:
        assert await _existe_tabla(s, "liquidaciones_obra") is True
        assert await _cuenta_cols(s, "gastos", _COLS_GASTOS) == len(_COLS_GASTOS)
