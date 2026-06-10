"""Loader idempotente del pack POS (ADR 0011 §D3): manifiesto → catálogo de retail.

Vuelve declarativa la siembra de un catálogo de ferretería/retail (productos, fracciones, aliases e
inventario de apertura). Driver SYNC; la `conn` debe traer `row_factory=dict_row` (como abre
`provision_from_manifest`). El commit lo hace el llamador (un solo commit por tenant).

Orden de escritura (respeta las FKs): productos → productos_fracciones → aliases → inventario.
Idempotencia por CLAVE NATURAL (no por id), igual que los demás loaders:
- productos: `codigo` si existe; si no, `nombre` NORMALIZADO (lower/trim/colapso de espacios). Re-correr
  con el mismo manifiesto ACTUALIZA la misma fila (no duplica) y editar el YAML propaga el cambio.
- productos_fracciones: UPSERT por (producto_id, fraccion) — UNIQUE `uq_producto_fraccion`.
- aliases: UPSERT por `termino` (UNIQUE); `producto` (nombre) → `producto_id` resuelto del lote.
- inventario: el `stock_inicial` se siembra CON su movimiento ENTRADA (regla 7 de CLAUDE.md: nada
  toca stock sin movimiento). La idempotencia la da `idempotency_key` derivada de la clave natural:
  el movimiento se inserta una sola vez y solo entonces se suma al stock.

Dinero (precio_venta, precio_compra, escalonado) va a columnas MONEY → Decimal al escribir. Cantidades
(decimal de fracción, umbral, stock_inicial) van a columnas QTY → Decimal(str(...)) para no arrastrar
el ruido binario del float.
"""
from __future__ import annotations

from decimal import Decimal

from core.logging import get_logger
# `normalizar_nombre` vive en el schema (helper puro, como `slug_valido`): la usan el loader (clave
# natural del upsert) y la validación, y el lookup SQL la espeja para encontrar la fila ya insertada.
from tools.manifest.schema import PackPos, ProductoPos, normalizar_nombre

log = get_logger("manifest.packs.pos")


def _dec(valor: int | float | None) -> Decimal | None:
    """Pesos/cantidad → Decimal (None pasa). `str()` evita el ruido binario de float (0.1, 0.25…)."""
    return None if valor is None else Decimal(str(valor))


def _id_producto(conn, p: ProductoPos) -> int | None:
    """Busca el id por clave natural: `codigo` (UNIQUE) si está; si no, `nombre` normalizado."""
    if p.codigo is not None:
        row = conn.execute("SELECT id FROM productos WHERE codigo = %s", (p.codigo,)).fetchone()
        return row["id"] if row else None
    row = conn.execute(
        "SELECT id FROM productos "
        r"WHERE lower(btrim(regexp_replace(nombre, '\s+', ' ', 'g'))) = %s",
        (normalizar_nombre(p.nombre),),
    ).fetchone()
    return row["id"] if row else None


def _columnas_producto(p: ProductoPos) -> dict:
    """Mapea un ProductoPos a las columnas de `productos`. Escalonado presente → tres columnas; ausente
    → las tres NULL (idempotente: re-correr sin escalonado las limpia)."""
    esc = p.escalonado
    return {
        "codigo": p.codigo,
        "nombre": p.nombre,
        "categoria": p.categoria,
        "unidad_medida": p.unidad_medida,
        "precio_venta": _dec(p.precio_venta),
        "precio_compra": _dec(p.precio_compra),
        "iva": p.iva,
        "permite_fraccion": p.permite_fraccion,
        "precio_umbral": _dec(esc.umbral) if esc else None,
        "precio_bajo_umbral": _dec(esc.bajo) if esc else None,
        "precio_sobre_umbral": _dec(esc.sobre) if esc else None,
    }


def _upsert_producto(conn, p: ProductoPos) -> int:
    """INSERT-si-ausente / UPDATE-si-existe por clave natural. Devuelve el `producto_id`."""
    cols = _columnas_producto(p)
    existente = _id_producto(conn, p)
    if existente is not None:
        conn.execute(
            "UPDATE productos SET codigo=%(codigo)s, nombre=%(nombre)s, categoria=%(categoria)s, "
            "unidad_medida=%(unidad_medida)s, precio_venta=%(precio_venta)s, "
            "precio_compra=%(precio_compra)s, iva=%(iva)s, permite_fraccion=%(permite_fraccion)s, "
            "precio_umbral=%(precio_umbral)s, precio_bajo_umbral=%(precio_bajo_umbral)s, "
            "precio_sobre_umbral=%(precio_sobre_umbral)s, activo=true, actualizado_en=now() "
            "WHERE id=%(id)s",
            {**cols, "id": existente},
        )
        return existente
    return conn.execute(
        "INSERT INTO productos (codigo, nombre, categoria, unidad_medida, precio_venta, precio_compra, "
        "iva, permite_fraccion, precio_umbral, precio_bajo_umbral, precio_sobre_umbral, activo) "
        "VALUES (%(codigo)s, %(nombre)s, %(categoria)s, %(unidad_medida)s, %(precio_venta)s, "
        "%(precio_compra)s, %(iva)s, %(permite_fraccion)s, %(precio_umbral)s, %(precio_bajo_umbral)s, "
        "%(precio_sobre_umbral)s, true) RETURNING id",
        cols,
    ).fetchone()["id"]


def _cargar_fracciones(conn, producto_id: int, p: ProductoPos) -> int:
    """UPSERT de las fracciones por (producto_id, fraccion). Devuelve cuántas se escribieron."""
    for f in p.fracciones:
        conn.execute(
            "INSERT INTO productos_fracciones (producto_id, fraccion, decimal, precio_total, "
            "precio_unitario) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (producto_id, fraccion) DO UPDATE SET decimal=EXCLUDED.decimal, "
            "precio_total=EXCLUDED.precio_total, precio_unitario=EXCLUDED.precio_unitario",
            (producto_id, f.fraccion, _dec(f.decimal), _dec(f.precio_total), _dec(f.precio_unitario)),
        )
    return len(p.fracciones)


def _sembrar_stock_inicial(conn, producto_id: int, p: ProductoPos) -> None:
    """Inventario de apertura CON movimiento ENTRADA (regla 7). Idempotente vía `idempotency_key`: el
    movimiento se inserta una sola vez; solo cuando se inserta se aplica el delta al stock, así que
    re-correr no doble-cuenta. La clave deriva de la clave natural del producto (no del tiempo)."""
    if p.stock_inicial is None:
        return
    clave = f"manifest:stock_inicial:{p.codigo or normalizar_nombre(p.nombre)}"
    cantidad = _dec(p.stock_inicial)
    insertado = conn.execute(
        "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad, costo_unitario, referencia, "
        "idempotency_key) VALUES (%s, 'ENTRADA'::mov_inventario_tipo, %s, %s, %s, %s) "
        "ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING id",
        (producto_id, cantidad, _dec(p.precio_compra), "stock inicial (manifiesto)", clave),
    ).fetchone()
    if insertado is None:
        return  # el movimiento ya existía: no re-aplicar al stock
    conn.execute(
        "INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (%s, %s, 0) "
        "ON CONFLICT (producto_id) DO UPDATE SET "
        "stock_actual = inventario.stock_actual + EXCLUDED.stock_actual, actualizado_en=now()",
        (producto_id, cantidad),
    )


def _cargar_aliases(conn, pos: PackPos, ids_por_nombre: dict[str, int]) -> int:
    """UPSERT de aliases por `termino`. `producto` (nombre normalizado) → `producto_id` del lote."""
    for a in pos.aliases:
        producto_id = ids_por_nombre.get(normalizar_nombre(a.producto)) if a.producto else None
        conn.execute(
            "INSERT INTO aliases (termino, reemplazo, producto_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (termino) DO UPDATE SET reemplazo=EXCLUDED.reemplazo, "
            "producto_id=EXCLUDED.producto_id, actualizado_en=now()",
            (a.termino, a.reemplazo, producto_id),
        )
    return len(pos.aliases)


def cargar_pos(pos: PackPos, conn) -> dict[str, int]:
    """Upserta el pack POS sobre la BD del tenant (idempotente). Devuelve conteos para el resumen.

    `conn` es una conexión psycopg SYNC con `row_factory=dict_row`; el commit lo hace el llamador.
    """
    ids_por_nombre: dict[str, int] = {}
    n_fracciones = 0
    for p in pos.productos:
        producto_id = _upsert_producto(conn, p)
        ids_por_nombre[normalizar_nombre(p.nombre)] = producto_id
        n_fracciones += _cargar_fracciones(conn, producto_id, p)
        _sembrar_stock_inicial(conn, producto_id, p)

    n_aliases = _cargar_aliases(conn, pos, ids_por_nombre)

    conteos = {"productos": len(pos.productos), "fracciones": n_fracciones, "aliases": n_aliases}
    log.info("pack_pos_cargado", **conteos)
    return conteos
