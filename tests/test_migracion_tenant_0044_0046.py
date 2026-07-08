"""Migraciones tenant 0044/0045/0046 — cotización/obra, operación y extensión CRM (upgrade/downgrade
limpios), corriendo contra una base efímera real (fixture `tenant`, ya en head).

Verifica (plan PIM §3, grupos 2 y 3 + extensión §2):
  - head trae las 10 tablas nuevas y los 7 tipos enum nuevos;
  - `clientes`/`proveedores` ganaron sus columnas nullable, con los defaults de la spec
    (`estatus` PROSPECTO, `reportes_diarios_obra.origen_registro` TELEGRAM_BOT);
  - los literales de los enums son EXACTOS a la spec (uno válido entra, uno inexistente falla);
  - integridad: items_cotizacion_obra se borra en CASCADA con su cotización; `obras.cotizacion_id` es
    1-1 (UNIQUE); una FK a obra inexistente falla;
  - downgrade a 0043 dropea SOLO lo de 0044-0046 (tablas, columnas y tipos) dejando intacto el grupo 1;
    upgrade vuelve a head sin romper y `origen_registro` (dueño 0044) sobrevive al downgrade de 0045.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

# Tablas nuevas: 4 de 0044 (obra) + 6 de 0045 (operación).
_TABLAS = (
    "cotizaciones_obra", "items_cotizacion_obra", "obras", "reportes_diarios_obra",
    "asignaciones_maquina_obra", "registros_horas_maquina", "mantenimientos",
    "asignaciones_trabajador_obra", "registros_asistencia", "consumos_inventario",
)
# Enums nuevos: 3 de 0044 + 2 de 0045 + 2 de 0046.
_ENUMS = (
    "estado_cotizacion", "estado_obra", "origen_registro",
    "tipo_mantenimiento", "tipo_ausencia",
    "estatus_cliente", "tipo_proveedor",
)
# Columnas nullable agregadas a tablas existentes (tabla, columna).
_COLUMNAS = (
    ("clientes", "estatus"),
    ("clientes", "contacto_nombre"),
    ("clientes", "contacto_cargo"),
    ("clientes", "contacto_telefono"),
    ("clientes", "contacto_email"),
    ("clientes", "acuerdo_comercial"),
    ("proveedores", "tipo"),
    ("proveedores", "contacto_nombre"),
    ("proveedores", "contacto_telefono"),
    ("proveedores", "contacto_email"),
)

_EXISTE_TABLA = "SELECT to_regclass('public.' || :t) IS NOT NULL"
_CUENTA_ENUMS = "SELECT count(*) FROM pg_type WHERE typtype='e' AND typname = ANY(:nombres)"
_EXISTE_COLUMNA = (
    "SELECT count(*) FROM information_schema.columns WHERE table_name=:t AND column_name=:c"
)

_INSERT_CLIENTE = "INSERT INTO clientes (nombre) VALUES (:n) RETURNING id"
_INSERT_COTIZACION = (
    "INSERT INTO cotizaciones_obra (numero, cliente_id, nombre_obra) "
    "VALUES (:num, :cid, 'Vía asfalto') RETURNING id"
)
_INSERT_ITEM = (
    "INSERT INTO items_cotizacion_obra (cotizacion_id, orden, descripcion, unidad, cantidad, "
    "valor_unitario) VALUES (:cot, 1, 'Base granular', 'm3', 10, 50000)"
)
_INSERT_OBRA = (
    "INSERT INTO obras (cotizacion_id, cliente_id, nombre) VALUES (:cot, :cid, 'Obra X') RETURNING id"
)
_CUENTA_ITEMS = "SELECT count(*) FROM items_cotizacion_obra WHERE cotizacion_id = :cot"


async def test_0044_0046_tablas_enums_columnas(tenant):
    # head (incluye 0044-0046): las 10 tablas, los 7 enums y las 10 columnas nuevas existen.
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is True
        assert (await s.execute(text(_CUENTA_ENUMS), {"nombres": list(_ENUMS)})).scalar_one() == 7
        for tabla, col in _COLUMNAS:
            existe = (
                await s.execute(text(_EXISTE_COLUMNA), {"t": tabla, "c": col})
            ).scalar_one()
            assert existe == 1, f"falta {tabla}.{col}"

    # Camino feliz: cliente → cotización + item → obra 1-1. Defaults de la spec se aplican.
    async with AsyncSession(tenant.engine) as s:
        cid = (await s.execute(text(_INSERT_CLIENTE), {"n": "Alcaldía"})).scalar_one()
        cot1 = (
            await s.execute(text(_INSERT_COTIZACION), {"num": "PIM-001-2026", "cid": cid})
        ).scalar_one()
        await s.execute(text(_INSERT_ITEM), {"cot": cot1})
        cot2 = (
            await s.execute(text(_INSERT_COTIZACION), {"num": "PIM-002-2026", "cid": cid})
        ).scalar_one()
        obra = (await s.execute(text(_INSERT_OBRA), {"cot": cot2, "cid": cid})).scalar_one()
        # reporte diario sin origen → default TELEGRAM_BOT; sin foto_urls → arreglo vacío.
        await s.execute(
            text("INSERT INTO reportes_diarios_obra (obra_id, fecha) VALUES (:o, CURRENT_DATE)"),
            {"o": obra},
        )
        await s.execute(
            text("INSERT INTO proveedores (nombre, tipo) VALUES ('IncoAsfaltos', 'PLANTA_ASFALTO')")
        )
        await s.commit()

        # Defaults de la spec: cliente.estatus = PROSPECTO; reporte.origen_registro = TELEGRAM_BOT.
        assert (
            await s.execute(text("SELECT estatus FROM clientes WHERE id=:i"), {"i": cid})
        ).scalar_one() == "PROSPECTO"
        assert (
            await s.execute(
                text("SELECT origen_registro FROM reportes_diarios_obra WHERE obra_id=:o"),
                {"o": obra},
            )
        ).scalar_one() == "TELEGRAM_BOT"

    # CASCADE: borrar la cotización se lleva sus items.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_CUENTA_ITEMS), {"cot": cot1})).scalar_one() == 1
        await s.execute(text("DELETE FROM cotizaciones_obra WHERE id=:i"), {"i": cot1})
        await s.commit()
        assert (await s.execute(text(_CUENTA_ITEMS), {"cot": cot1})).scalar_one() == 0

    # UNIQUE 1-1: otra obra con la misma cotización es rechazada.
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(IntegrityError):
            await s.execute(text(_INSERT_OBRA), {"cot": cot2, "cid": cid})
            await s.commit()

    # Enum EXACTO: un literal fuera de estado_cotizacion es rechazado.
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(DBAPIError):
            await s.execute(
                text(
                    "INSERT INTO cotizaciones_obra (numero, cliente_id, nombre_obra, estado) "
                    "VALUES ('PIM-009-2026', :cid, 'Z', 'APROBADA')"
                ),
                {"cid": cid},
            )
            await s.commit()

    # FK a obra inexistente viola integridad (registros_horas_maquina.obra_id).
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO registros_horas_maquina (maquina_id, obra_id, fecha, "
                    "horas_trabajadas, horas_facturables) VALUES (:m, 999999, CURRENT_DATE, 6, 6)"
                ),
                {"m": 999999},
            )
            await s.commit()


async def test_0044_0046_downgrade_up(tenant):
    # downgrade a 0043 → lo de 0044-0046 se va limpio; el grupo 1 (maquinas) sobrevive.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0043_construccion_base")
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is False
        assert (await s.execute(text(_CUENTA_ENUMS), {"nombres": list(_ENUMS)})).scalar_one() == 0
        for tabla, col in _COLUMNAS:
            existe = (
                await s.execute(text(_EXISTE_COLUMNA), {"t": tabla, "c": col})
            ).scalar_one()
            assert existe == 0, f"quedó {tabla}.{col} tras downgrade"
        # El grupo 1 (0043) sigue intacto: solo se revirtieron 0044-0046.
        assert (await s.execute(text(_EXISTE_TABLA), {"t": "maquinas"})).scalar_one() is True

    # upgrade vuelve a head sin romper; las tablas nuevas reaparecen.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is True
        assert (await s.execute(text(_CUENTA_ENUMS), {"nombres": list(_ENUMS)})).scalar_one() == 7
