"""Migraciones: upgrade/downgrade corren limpio en control y en tenant (.claude/rules/testing.md)."""
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database
from tools._alembic import downgrade_tenant, upgrade_tenant

_BASE_TABLES = (
    "SELECT count(*) FROM information_schema.tables "
    "WHERE table_schema='public' AND table_type='BASE TABLE'"
)


async def test_tenant_upgrade_downgrade_limpio(tenant):
    # `tenant` ya viene migrado a head (lo hace el fixture).
    async with AsyncSession(tenant.engine) as s:
        tablas = (await s.execute(text(_BASE_TABLES))).scalar_one()
        seqs = (await s.execute(text("SELECT count(*) FROM pg_sequences WHERE schemaname='public'"))).scalar_one()
        enums = (await s.execute(text("SELECT count(*) FROM pg_type WHERE typtype='e'"))).scalar_one()
    assert tablas >= 35
    assert seqs >= 3          # ventas/fe/ds consecutivos
    # 6 del pack Agenda/Citas (recurso_tipo, cita_estado, cita_origen, modo_confirmacion,
    # anticipo_tipo y cita_confirmacion —este último añadido por 0011_reconfirmacion—) + 1 del
    # handoff (conversacion_estado, 0009_conversaciones) + 1 del pack cobranza
    # (promesa_estado, 0017_cobranza) + 1 del pack pedidos (pedido_estado, 0019) + 1 del pack
    # ventas/cotizaciones (cotizacion_estado, 0020) + 1 de pagos (cobro_estado, 0021) + 2 del inbox
    # de conversación (mensaje_direccion, mensaje_autor, 0024_conversacion_mensajes) = 24, + 1 de la
    # conciliación bancaria (conciliacion_estado, 0035, ADR 0028) = 25, + 3 del vertical construcción
    # (tipo_vinculacion, estado_maquina, estado_herramienta, 0043_construccion_base) = 28, + 3 de
    # cotización/obra (estado_cotizacion, estado_obra, origen_registro, 0044_construccion_obra) = 31,
    # + 2 de operación (tipo_mantenimiento, tipo_ausencia, 0045_construccion_operacion) = 33, + 2 de la
    # extensión CRM (estatus_cliente, tipo_proveedor, 0046_ext_clientes_proveedores) = 35, + 2 de nómina
    # (estado_periodo_nomina, tipo_periodo_nomina, 0047_nomina) = 37, + 4 de la imputación a obra en
    # gastos/compras + liquidación (categoria_gasto, metodo_pago_gasto, categoria_compra, semaforo_obra,
    # 0048_gastos_compras_liquidacion). Total: 41.
    assert enums == 41

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "base")

    async with AsyncSession(tenant.engine) as s:
        # Solo queda alembic_version; el esquema de negocio se fue.
        assert (await s.execute(text(_BASE_TABLES))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM pg_type WHERE typtype='e'"))).scalar_one() == 0


_COL_IDEM = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='movimientos_inventario' AND column_name='idempotency_key'"
)
_IDX_IDEM = "SELECT count(*) FROM pg_indexes WHERE indexname='uq_mov_inv_idempotency_key'"


async def test_0002_idempotency_key_up_down(tenant):
    # `tenant` viene en head (incluye 0002): la columna y el índice UNIQUE parcial existen.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_IDEM))).scalar_one() == 1
        assert (await s.execute(text(_IDX_IDEM))).scalar_one() == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0001_tenant")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_IDEM))).scalar_one() == 0
        assert (await s.execute(text(_IDX_IDEM))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica 0002 limpio (idempotente para el fixture)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_IDEM))).scalar_one() == 1


_TBL_MSGS = "SELECT to_regclass('public.conversacion_mensajes') IS NOT NULL"
_ENUMS_MSGS = (
    "SELECT count(*) FROM pg_type WHERE typtype='e' AND typname IN ('mensaje_direccion','mensaje_autor')"
)


async def test_0024_conversacion_mensajes_up_down(tenant):
    # head incluye 0024: la tabla del hilo y sus 2 enums existen.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_TBL_MSGS))).scalar_one() is True
        assert (await s.execute(text(_ENUMS_MSGS))).scalar_one() == 2

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0023_postventa")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_TBL_MSGS))).scalar_one() is False
        assert (await s.execute(text(_ENUMS_MSGS))).scalar_one() == 0   # los tipos se fueron con la tabla

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio (idempotente para el fixture)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_TBL_MSGS))).scalar_one() is True


_TABLAS_0003 = ("caja_movimientos", "gastos", "fiados", "fiados_movimientos")


async def test_0003_dinero_idempotency_up_down(tenant):
    # head incluye 0003: las 4 tablas de dinero tienen idempotency_key + su índice UNIQUE parcial.
    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS_0003:
            col = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name=:t AND column_name='idempotency_key'"
                    ),
                    {"t": tabla},
                )
            ).scalar_one()
            idx = (
                await s.execute(
                    text("SELECT count(*) FROM pg_indexes WHERE indexname=:i"),
                    {"i": f"uq_{tabla}_idempotency_key"},
                )
            ).scalar_one()
            assert col == 1 and idx == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0002_mov_inv_idem")
    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS_0003:
            col = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name=:t AND column_name='idempotency_key'"
                    ),
                    {"t": tabla},
                )
            ).scalar_one()
            assert col == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio


def test_control_upgrade_downgrade_limpio(monkeypatch):
    name = f"test_control_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    try:
        cfg = Config("migrations/control/alembic.ini")
        command.upgrade(cfg, "head")
        with psycopg.connect(to_libpq(url), autocommit=True) as conn:
            existe = conn.execute("SELECT to_regclass('public.empresas') IS NOT NULL").fetchone()[0]
            assert existe is True
        command.downgrade(cfg, "base")
        with psycopg.connect(to_libpq(url), autocommit=True) as conn:
            existe = conn.execute("SELECT to_regclass('public.empresas') IS NOT NULL").fetchone()[0]
            assert existe is False
    finally:
        get_settings.cache_clear()
        drop_database(name)
