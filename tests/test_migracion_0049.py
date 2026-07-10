"""Migración 0049 (cartera de alquiler + cierre M2 idempotencia del consumo): up/down/up limpio en base
efímera (.claude/rules/testing.md).

Verifica que head trae: (a) las 3 tablas nuevas (`cupos_alquiler`, `cargos_alquiler`, `cartera_config`);
(b) el índice ÚNICO PARCIAL `uq_cupos_alquiler_cliente_activo` (un cupo activo por cliente) —y que de veras
es PARCIAL (tiene predicado WHERE); (c) la columna `consumos_inventario.idempotency_key` + su índice único
parcial. Y que el downgrade a 0048 lo retira TODO —las 3 tablas y la columna/índice de consumos— dejando
`consumos_inventario` en pie. Reaplica head limpio al final.

Dos pruebas FUNCIONALES cierran los invariantes que anclan estos índices:
  - `uq_cupos_alquiler_cliente_activo`: dos cupos ACTIVOS para el mismo cliente chocan; uno inactivo entra.
  - `UNIQUE(cargos_alquiler.registro_horas_id)`: dos cargos para el mismo registro de horas chocan (ancla
    dura del invariante «un registro de horas no genera dos cargos en cartera»).
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_TABLAS = ("cupos_alquiler", "cargos_alquiler", "cartera_config")


async def _existe_tabla(s: AsyncSession, tabla: str) -> bool:
    return (
        await s.execute(text(f"SELECT to_regclass('public.{tabla}') IS NOT NULL"))
    ).scalar_one()


async def _tiene_col(s: AsyncSession, tabla: str, col: str) -> bool:
    return (
        await s.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c"
            ),
            {"t": tabla, "c": col},
        )
    ).scalar_one() == 1


async def test_0049_up_down_up(tenant):
    # head incluye 0049: tablas, columna e índices existen.
    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS:
            assert await _existe_tabla(s, tabla) is True
        assert await _tiene_col(s, "consumos_inventario", "idempotency_key") is True

        # Índice único PARCIAL de cupos: existe y su definición trae el predicado WHERE (es parcial).
        cupo_idx = (
            await s.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname='uq_cupos_alquiler_cliente_activo'"
                )
            )
        ).scalar_one_or_none()
        assert cupo_idx is not None and "WHERE" in cupo_idx.upper()

        # Índice único parcial de la idempotencia del consumo (M2).
        cons_idx = (
            await s.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname='uq_consumos_inventario_idempotency_key'"
                )
            )
        ).scalar_one_or_none()
        assert cons_idx is not None and "WHERE" in cons_idx.upper()

        # Ancla dura del invariante «un parte no genera dos cargos». 0049 la creó como
        # UNIQUE(registro_horas_id); 0054 (turnos) la reemplaza por DOS índices únicos parciales
        # (cargo del parte sin turno / cargo por turno). El test corre en head → forma post-0054.
        uq_cargos = (
            await s.execute(
                text(
                    "SELECT count(*) FROM pg_indexes WHERE tablename='cargos_alquiler' "
                    "AND indexname IN ('uq_cargos_alquiler_registro_sin_turno', 'uq_cargos_alquiler_turno')"
                )
            )
        ).scalar_one()
        assert uq_cargos == 2

        # FK cargos_alquiler.registro_horas_id → registros_horas_maquina.
        fk = (
            await s.execute(
                text(
                    "SELECT count(*) FROM information_schema.key_column_usage k "
                    "JOIN information_schema.table_constraints c "
                    "  ON k.constraint_name=c.constraint_name "
                    "WHERE c.table_name='cargos_alquiler' AND c.constraint_type='FOREIGN KEY' "
                    "AND k.column_name='registro_horas_id'"
                )
            )
        ).scalar_one()
        assert fk == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0048_gastos_compras_liquidacion")

    async with AsyncSession(tenant.engine) as s:
        for tabla in _TABLAS:
            assert await _existe_tabla(s, tabla) is False
        assert await _tiene_col(s, "consumos_inventario", "idempotency_key") is False
        # consumos_inventario sobrevive (solo se quitó la columna del M2).
        assert await _existe_tabla(s, "consumos_inventario") is True
        # el índice parcial del M2 se fue con la columna.
        idx = (
            await s.execute(
                text(
                    "SELECT count(*) FROM pg_indexes "
                    "WHERE indexname='uq_consumos_inventario_idempotency_key'"
                )
            )
        ).scalar_one()
        assert idx == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio (idempotente para el fixture)
    async with AsyncSession(tenant.engine) as s:
        assert await _existe_tabla(s, "cupos_alquiler") is True
        assert await _tiene_col(s, "consumos_inventario", "idempotency_key") is True


async def test_0049_cupo_activo_unico_por_cliente(tenant):
    """El índice único parcial permite muchos cupos INACTIVOS pero un solo ACTIVO por cliente."""
    async with AsyncSession(tenant.engine) as s:
        cid = (
            await s.execute(
                text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
                "VALUES (:c, 10000000, CURRENT_DATE, true)"
            ),
            {"c": cid},
        )
        await s.commit()

        # Un segundo cupo ACTIVO para el mismo cliente choca contra el único parcial.
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
                    "VALUES (:c, 20000000, CURRENT_DATE, true)"
                ),
                {"c": cid},
            )
        await s.rollback()

        # Un cupo INACTIVO para el mismo cliente sí entra (histórico).
        await s.execute(
            text(
                "INSERT INTO cupos_alquiler (cliente_id, cupo, vigente_desde, activo) "
                "VALUES (:c, 20000000, CURRENT_DATE, false)"
            ),
            {"c": cid},
        )
        await s.commit()

        total = (
            await s.execute(
                text("SELECT count(*) FROM cupos_alquiler WHERE cliente_id=:c"), {"c": cid}
            )
        ).scalar_one()
        activos = (
            await s.execute(
                text("SELECT count(*) FROM cupos_alquiler WHERE cliente_id=:c AND activo"), {"c": cid}
            )
        ).scalar_one()
        assert total == 2 and activos == 1


async def test_0049_cargo_unico_por_registro_horas(tenant):
    """UNIQUE(registro_horas_id): un mismo `RegistroHorasMaquina` no puede tener dos cargos de alquiler
    (ancla dura del invariante de idempotencia, a nivel de base)."""
    async with AsyncSession(tenant.engine) as s:
        cid = (
            await s.execute(
                text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
            )
        ).scalar_one()
        oid = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, 'Vía La Paz') RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        mid = (
            await s.execute(
                text(
                    "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
                    "VALUES ('M-1', 'Vibrocompactador', 'compactador', 120000) RETURNING id"
                )
            )
        ).scalar_one()
        aid = (
            await s.execute(
                text(
                    "INSERT INTO asignaciones_maquina_obra "
                    "(maquina_id, obra_id, fecha_inicio, precio_hora, minimo_horas) "
                    "VALUES (:m, :o, CURRENT_DATE, 120000, 4) RETURNING id"
                ),
                {"m": mid, "o": oid},
            )
        ).scalar_one()
        rid = (
            await s.execute(
                text(
                    "INSERT INTO registros_horas_maquina "
                    "(maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
                    "VALUES (:m, :o, CURRENT_DATE, 8, 8) RETURNING id"
                ),
                {"m": mid, "o": oid},
            )
        ).scalar_one()
        fid = (
            await s.execute(
                text(
                    "INSERT INTO fiados (cliente_id, monto, saldo) VALUES (:c, 960000, 960000) RETURNING id"
                ),
                {"c": cid},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO cargos_alquiler "
                "(registro_horas_id, fiado_id, obra_id, maquina_id, asignacion_id, monto) "
                "VALUES (:r, :f, :o, :m, :a, 960000)"
            ),
            {"r": rid, "f": fid, "o": oid, "m": mid, "a": aid},
        )
        await s.commit()

        # Un segundo cargo para el MISMO registro de horas choca contra el UNIQUE.
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO cargos_alquiler "
                    "(registro_horas_id, fiado_id, obra_id, maquina_id, asignacion_id, monto) "
                    "VALUES (:r, :f, :o, :m, :a, 960000)"
                ),
                {"r": rid, "f": fid, "o": oid, "m": mid, "a": aid},
            )
        await s.rollback()

        n = (
            await s.execute(
                text("SELECT count(*) FROM cargos_alquiler WHERE registro_horas_id=:r"), {"r": rid}
            )
        ).scalar_one()
        assert n == 1
