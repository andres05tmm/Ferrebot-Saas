"""Carga idempotente al tenant: upsert por PK preservada, transacción por tabla (spec §2).

- `INSERT ... ON CONFLICT DO NOTHING` (sin target: cualquier UNIQUE/PK protege la re-corrida).
- Enums del destino se castean explícitamente (psycopg envía str como text).
- **Preflight:** si el destino tiene filas cuya PK no está en el origen (p.ej. seeds del
  manifiesto), aborta con `DestinoNoVacioError` — cargar encima mezclaría catálogos con IDs
  cruzados y `verify` jamás daría paridad. Con `limpiar=True` se barren las tablas del ETL
  (TRUNCATE ... CASCADE, logueando el radio de impacto) y se carga sobre limpio.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from core.db.urls import to_libpq
from core.logging import get_logger

log = get_logger("etl_puntorojo.load")

# Orden de carga (respeta FKs; spec §5). También define qué tablas "posee" el ETL.
ORDEN_CARGA = [
    "usuarios", "clientes", "productos",
    "productos_fracciones", "aliases", "inventario", "movimientos_inventario",
    "proveedores", "facturas_proveedores", "facturas_abonos",
    "ventas", "ventas_detalle", "historico_ventas",
    "facturas_electronicas", "cuentas_cobro", "documentos_soporte",
    "compras", "compras_detalle", "compras_fiscal",
    "gastos", "caja",
    "fiados", "fiados_movimientos",
    "bancolombia_transferencias", "iva_saldos_bimestrales", "memoria_entidades",
]

# Columna PK lógica por tabla (para preflight); None = sin preflight (sin PK preservada útil).
_PK = {t: "id" for t in ORDEN_CARGA} | {
    "inventario": "producto_id",
    "historico_ventas": "fecha",
    "aliases": "termino",
}

# Casts a enums del destino: psycopg manda str como text y Postgres no coerciona solo.
_CASTS = {
    ("usuarios", "rol"): "usuario_rol",
    ("ventas", "metodo_pago"): "metodo_pago",
    ("ventas", "estado"): "venta_estado",
    ("ventas", "origen"): "venta_origen",
    ("facturas_electronicas", "tipo"): "fe_tipo",
    ("facturas_electronicas", "estado"): "fe_estado",
    ("gastos", "categoria"): "gasto_categoria",
    ("caja", "estado"): "caja_estado",
    ("movimientos_inventario", "tipo"): "mov_inventario_tipo",
    ("bancolombia_transferencias", "estado_conciliacion"): "conciliacion_estado",
    ("fiados_movimientos", "tipo"): "fiado_mov_tipo",
}

_JSONB = {("facturas_electronicas", "dian_respuesta"), ("memoria_entidades", "valor")}


class DestinoNoVacioError(RuntimeError):
    """El destino tiene filas ajenas al origen: cargar encima rompería la paridad."""


@dataclass(slots=True)
class ReporteTabla:
    leidas: int = 0
    insertadas: int = 0
    saltadas: int = 0


def _conectar(url: str) -> psycopg.Connection:
    return psycopg.connect(to_libpq(url), row_factory=dict_row)


def _preparar_fila(tabla: str, fila: dict) -> dict:
    return {c: Json(v) if (tabla, c) in _JSONB and v is not None else v for c, v in fila.items()}


def _insertar_tabla(conn: psycopg.Connection, tabla: str, filas: list[dict]) -> ReporteTabla:
    reporte = ReporteTabla(leidas=len(filas))
    if not filas:
        return reporte
    columnas = list(filas[0].keys())
    placeholders = ", ".join(
        f"%({c})s::{_CASTS[(tabla, c)]}" if (tabla, c) in _CASTS else f"%({c})s" for c in columnas
    )
    sql = (f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({placeholders}) "
           "ON CONFLICT DO NOTHING")
    with conn.transaction():
        for fila in filas:
            cur = conn.execute(sql, _preparar_fila(tabla, fila))
            reporte.insertadas += cur.rowcount
    reporte.saltadas = reporte.leidas - reporte.insertadas
    return reporte


def _filas_ajenas(conn: psycopg.Connection, tabla: str, filas: list[dict]) -> list:
    """PKs presentes en destino que NO vienen en el origen (datos ajenos al ETL)."""
    pk = _PK.get(tabla)
    if pk is None:
        return []
    existentes = {r[pk] for r in conn.execute(f"SELECT {pk} FROM {tabla}")}
    del_origen = {f[pk] for f in filas}
    return sorted(existentes - del_origen, key=str)[:10]


def verificar_destino(conn: psycopg.Connection, datos: dict[str, list[dict]]) -> None:
    problemas = []
    for tabla in ORDEN_CARGA:
        ajenas = _filas_ajenas(conn, tabla, datos.get(tabla, []))
        if ajenas:
            problemas.append(f"{tabla}: {ajenas}")
    if problemas:
        raise DestinoNoVacioError(
            "el destino tiene filas ajenas al origen (¿seeds del manifiesto?); "
            "usa --limpiar para barrer las tablas del ETL antes de cargar. Muestra: "
            + "; ".join(problemas)
        )


def _limpiar(conn: psycopg.Connection) -> None:
    """TRUNCATE CASCADE de las tablas del ETL, logueando qué tablas arrastra la cascada."""
    tablas = ", ".join(ORDEN_CARGA)
    dependientes = conn.execute(
        """
        SELECT DISTINCT c.conrelid::regclass::text
        FROM pg_constraint c
        WHERE c.contype = 'f'
          AND c.confrelid::regclass::text = ANY(%s)
          AND c.conrelid::regclass::text <> ALL(%s)
        """,
        (ORDEN_CARGA, ORDEN_CARGA),
    ).fetchall()
    if dependientes:
        log.warning("limpiar: la cascada tocará también %s",
                    [d["conrelid"] for d in dependientes])
    with conn.transaction():
        conn.execute(f"TRUNCATE {tablas} CASCADE")
    log.info("limpiar: tablas del ETL truncadas (%d)", len(ORDEN_CARGA))


def cargar(url_tenant: str, datos: dict[str, list[dict]], *, limpiar: bool = False,
           ) -> dict[str, ReporteTabla]:
    """Carga todas las tablas en orden FK. Devuelve el reporte por tabla."""
    reportes: dict[str, ReporteTabla] = {}
    with _conectar(url_tenant) as conn:
        if limpiar:
            _limpiar(conn)
        else:
            verificar_destino(conn, datos)
        for tabla in ORDEN_CARGA:
            filas = datos.get(tabla, [])
            reportes[tabla] = _insertar_tabla(conn, tabla, filas)
            r = reportes[tabla]
            log.info("carga %s: leidas=%d insertadas=%d saltadas=%d",
                     tabla, r.leidas, r.insertadas, r.saltadas)
    return reportes
