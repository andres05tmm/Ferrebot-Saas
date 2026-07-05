"""Lectura del origen FerreBot (dump restaurado o réplica) — SOLO LECTURA (spec §2).

La transacción se abre `read only`: este módulo jamás escribe en la base origen.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from core.logging import get_logger

log = get_logger("etl_puntorojo.extract")

# Tablas del legacy que consume el ETL (las operativas de IA se descartan — D11).
TABLAS_ORIGEN = [
    "usuarios", "productos", "productos_fracciones", "aliases", "clientes",
    "inventario", "ventas", "ventas_detalle", "historico_ventas",
    "facturas_electronicas", "cuentas_cobro", "documentos_soporte",
    "compras", "compras_fiscal", "facturas_proveedores", "facturas_abonos",
    "gastos", "caja", "fiados", "fiados_movimientos",
    "bancolombia_transferencias", "iva_saldos_bimestrales", "memoria_entidades",
]


def leer_origen(url: str) -> dict[str, list[dict]]:
    """Lee todas las tablas del origen a memoria (volúmenes chicos: <1k filas por tabla)."""
    origen: dict[str, list[dict]] = {}
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET default_transaction_read_only = on")
        for tabla in TABLAS_ORIGEN:
            origen[tabla] = conn.execute(f"SELECT * FROM {tabla} ORDER BY 1").fetchall()
            log.info("origen %s: %d filas", tabla, len(origen[tabla]))
    return origen
