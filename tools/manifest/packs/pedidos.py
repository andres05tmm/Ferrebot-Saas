"""Loader idempotente del pack Pedidos (ADR 0016): manifiesto → config de cocina + zonas de domicilio.

El MENÚ NO se siembra aquí: es el catálogo del POS (`packs.pos.productos` → tabla `productos`), que el
pack de pedidos solo LEE. Este loader cubre lo OPERATIVO del pack: la única fila de `pedido_config`
(horario de cocina, mínimo, tiempo estimado, domicilio default) y las `zonas_domicilio` (barrio→tarifa).

Driver SYNC; la `conn` debe traer `row_factory=dict_row` (como abre `provision_from_manifest`). El commit
lo hace el llamador. Idempotente por clave natural:
- pedido_config: una sola fila (la primera por id) — INSERT-si-ausente / UPDATE-si-existe.
- zonas_domicilio: UPSERT por `nombre` (insert-si-ausente, paridad con los demás loaders por nombre).

Dinero (minimo_pedido, costo_domicilio_default, tarifa) va a columnas MONEY → Decimal al escribir.
"""
from __future__ import annotations

from decimal import Decimal

from core.logging import get_logger
from tools.manifest.schema import PackPedidos, PedidoConfig

log = get_logger("manifest.packs.pedidos")


def _upsert_config(conn, cfg: PedidoConfig) -> None:
    """INSERT-si-ausente / UPDATE-si-existe de la fila única de `pedido_config`. Dinero → Decimal."""
    params = {
        "activo": cfg.activo,
        "hora_apertura": cfg.hora_apertura,        # "HH:MM" → Time (PG castea el texto)
        "hora_cierre": cfg.hora_cierre,
        "minimo_pedido": Decimal(cfg.minimo_pedido),
        "tiempo_estimado_min": cfg.tiempo_estimado_min,
        "costo_domicilio_default": Decimal(cfg.costo_domicilio_default),
    }
    existente = conn.execute("SELECT id FROM pedido_config ORDER BY id LIMIT 1").fetchone()
    if existente is not None:
        conn.execute(
            "UPDATE pedido_config SET activo=%(activo)s, hora_apertura=%(hora_apertura)s, "
            "hora_cierre=%(hora_cierre)s, minimo_pedido=%(minimo_pedido)s, "
            "tiempo_estimado_min=%(tiempo_estimado_min)s, "
            "costo_domicilio_default=%(costo_domicilio_default)s WHERE id=%(id)s",
            {**params, "id": existente["id"]},
        )
    else:
        conn.execute(
            "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
            "tiempo_estimado_min, costo_domicilio_default) VALUES (%(activo)s, %(hora_apertura)s, "
            "%(hora_cierre)s, %(minimo_pedido)s, %(tiempo_estimado_min)s, %(costo_domicilio_default)s)",
            params,
        )


def _upsert_zonas(conn, pedidos: PackPedidos) -> int:
    """UPSERT de zonas de domicilio por `nombre` (tarifa MONEY → Decimal). Devuelve cuántas se declaran."""
    for z in pedidos.zonas:
        existente = conn.execute(
            "SELECT id FROM zonas_domicilio WHERE nombre = %s", (z.nombre,)
        ).fetchone()
        if existente is not None:
            conn.execute(
                "UPDATE zonas_domicilio SET tarifa=%s, recargo_por_item=%s, activo=true WHERE id=%s",
                (Decimal(z.tarifa), Decimal(z.recargo_por_item), existente["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO zonas_domicilio (nombre, tarifa, recargo_por_item, activo) "
                "VALUES (%s, %s, %s, true)",
                (z.nombre, Decimal(z.tarifa), Decimal(z.recargo_por_item)),
            )
    return len(pedidos.zonas)


def _upsert_mesas(conn, pedidos: PackPedidos) -> int:
    """UPSERT de mesas del salón por `nombre` (F3 / ADR 0032 D4)."""
    for nombre in pedidos.mesas:
        existente = conn.execute("SELECT id FROM mesas WHERE nombre = %s", (nombre,)).fetchone()
        if existente is not None:
            conn.execute("UPDATE mesas SET activo=true WHERE id=%s", (existente["id"],))
        else:
            conn.execute("INSERT INTO mesas (nombre, activo) VALUES (%s, true)", (nombre,))
    return len(pedidos.mesas)


def cargar_pedidos(pedidos: PackPedidos, conn) -> dict[str, int]:
    """Upserta la config operativa del pack Pedidos (idempotente). Devuelve conteos para el resumen.

    `conn` es una conexión psycopg SYNC con `row_factory=dict_row`; el commit lo hace el llamador.
    """
    _upsert_config(conn, pedidos.config)
    n_zonas = _upsert_zonas(conn, pedidos)
    n_mesas = _upsert_mesas(conn, pedidos)
    conteos = {"zonas": n_zonas, "mesas": n_mesas}
    log.info("pack_pedidos_cargado", **conteos)
    return conteos
