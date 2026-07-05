"""`setval` post-carga (spec §5): PKs seriales + consecutivos de negocio.

Conexión DIRECTA al tenant (no PgBouncer transaction-mode): `setval` es seguro igual, pero el
runbook lo corre con la URL directa (patrón `resembrar_demos`).
"""
from __future__ import annotations

import psycopg

from core.db.urls import to_libpq
from core.logging import get_logger

log = get_logger("etl_puntorojo.sequences")

_TABLAS_SERIAL = [
    "usuarios", "clientes", "productos", "productos_fracciones", "aliases",
    "movimientos_inventario", "proveedores", "facturas_abonos",
    "ventas", "ventas_detalle", "facturas_electronicas", "cuentas_cobro",
    "documentos_soporte", "compras", "compras_detalle", "compras_fiscal",
    "gastos", "caja", "fiados", "fiados_movimientos",
    "bancolombia_transferencias", "iva_saldos_bimestrales", "memoria_entidades",
]


def _setval(conn: psycopg.Connection, seq_sql: str, max_sql: str, params: tuple = ()) -> None:
    fila = conn.execute(max_sql, params).fetchone()
    maximo = fila[0] if fila else None
    if maximo and int(maximo) > 0:
        conn.execute(f"SELECT setval({seq_sql}, %s)", (*params, int(maximo)) if params else (int(maximo),))


def ajustar_secuencias(url_tenant: str) -> None:
    with psycopg.connect(to_libpq(url_tenant)) as conn:
        # PKs seriales/identity de todas las tablas cargadas.
        for tabla in _TABLAS_SERIAL:
            seq = conn.execute("SELECT pg_get_serial_sequence(%s, 'id')", (tabla,)).fetchone()[0]
            if seq is None:
                continue
            maximo = conn.execute(f"SELECT max(id) FROM {tabla}").fetchone()[0]
            if maximo:
                conn.execute("SELECT setval(%s, %s)", (seq, int(maximo)))
        # Consecutivos de negocio (no son la PK).
        _setval(conn, "'ventas_consecutivo_seq'", "SELECT max(consecutivo) FROM ventas")
        # El consecutivo LEGAL de factura: solo facturas reales (las 'ERR-*' no consumen numeración).
        _setval(conn, "'fe_factura_consecutivo_seq'",
                "SELECT max(consecutivo) FROM facturas_electronicas "
                "WHERE tipo = 'factura' AND prefijo <> 'ERR'")
        # DS: consecutivo embebido en texto ('DS-3') → máximo numérico.
        _setval(conn, "'ds_consecutivo_seq'",
                r"SELECT max(NULLIF(regexp_replace(consecutivo, '\D', '', 'g'), '')::bigint) "
                "FROM documentos_soporte")
        conn.commit()
    log.info("secuencias ajustadas (%d tablas + 3 consecutivos de negocio)", len(_TABLAS_SERIAL))
