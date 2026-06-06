"""ETL de un solo uso: migrar SOLO el catálogo de productos del FerreBot viejo al tenant Punto Rojo.

Alcance acotado (decisiones-migracion.md, recorte para este corte):
  - Migra `productos` + `productos_fracciones`. NADA de historial, ventas, clientes ni aliases.
  - Stock inicial 0 (una fila `inventario` por producto; el conteo físico se hace después).
  - Sin costo: `precio_compra` queda NULL. `precio_especial` y `proveedor_id` también (no existen en el viejo).

Uso:
    python -m tools.etl_productos --origen "<OLD_PUBLIC_URL>" --destino "<PR_TENANT_PUBLIC_URL>" [--dry-run]

  - Lee del ORIGEN solo con SELECT (sesión read-only; nunca escribe en el viejo).
  - --dry-run: mapea y REPORTA (cuántos productos, cuántas fracciones, ejemplos) SIN escribir.
  - Sin --dry-run: inserta en el DESTINO en una sola transacción. IDEMPOTENTE: dedup por `codigo`
    (si no es NULL) o por `nombre`; si el producto ya existe, SE OMITE (no pisa cambios del nuevo).

Driver sync psycopg (patrón de tools/provision_tenant.py); `to_libpq` para las URLs.
Las funciones de mapeo y dedup son PURAS y testeables (ver tests/test_etl_productos.py).
"""
import argparse
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from psycopg.rows import dict_row

from core.db.urls import to_libpq

# Tipos de comodidad para las estructuras en memoria.
ProductoMapeado = dict[str, Any]
FraccionMapeada = dict[str, Any]
Plan = list[tuple[ProductoMapeado, list[FraccionMapeada]]]


def _a_decimal(valor: Any) -> Decimal | None:
    """Convierte un valor numérico (int/Decimal/str) a Decimal; None se preserva como None."""
    if valor is None:
        return None
    return Decimal(str(valor))


def _normalizar_codigo(valor: Any) -> str | None:
    """Normaliza `codigo`: cadena vacía o solo-espacios cuenta como NULL (evita choques de UNIQUE)."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def parsear_fraccion(fraccion: Any) -> Decimal | None:
    """Convierte el texto de una fracción a su valor decimal (función PURA).

    "1/2"→0.5, "1/4"→0.25, "3/4"→0.75; numérico directo ("2"→2, "0.5"→0.5).
    Si no se puede interpretar (texto basura, división por cero, vacío), devuelve None.
    """
    if fraccion is None:
        return None
    texto = str(fraccion).strip()
    if not texto:
        return None
    if "/" in texto:
        numerador_txt, _, denominador_txt = texto.partition("/")
        try:
            numerador = Decimal(numerador_txt.strip())
            denominador = Decimal(denominador_txt.strip())
        except (InvalidOperation, ValueError):
            return None
        if denominador == 0:
            return None
        return numerador / denominador
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def mapear_producto(viejo: Mapping[str, Any], *, tiene_fracciones: bool) -> ProductoMapeado:
    """Mapea un producto del esquema viejo (FerreBot) al nuevo (tenant PR). Función PURA.

    Reglas (T-PRECIO, recorte de catálogo):
      - precio_venta = precio_unidad; umbrales (`precio_umbral/bajo/sobre`) directos, a Decimal.
      - iva = porcentaje_iva si tiene_iva, si no 0.
      - permite_fraccion = el producto tiene filas en productos_fracciones.
      - precio_compra / precio_especial / proveedor_id = NULL (no existen en el viejo).
      - `codigo` en blanco se trata como NULL; `unidad_medida` vacío usa 'Unidad'; `activo` None → True.
      - precio_venta cae a 0 solo si el origen no trae precio (defensivo: la columna es NOT NULL).
    """
    tiene_iva = bool(viejo.get("tiene_iva"))
    porcentaje = viejo.get("porcentaje_iva")
    iva = int(porcentaje) if (tiene_iva and porcentaje is not None) else 0

    precio_venta = _a_decimal(viejo.get("precio_unidad"))
    if precio_venta is None:
        precio_venta = Decimal("0")

    unidad = viejo.get("unidad_medida")
    activo = viejo.get("activo")
    return {
        "codigo": _normalizar_codigo(viejo.get("codigo")),
        "nombre": viejo.get("nombre"),
        "categoria": viejo.get("categoria"),
        "proveedor_id": None,
        "unidad_medida": unidad if unidad else "Unidad",
        "precio_venta": precio_venta,
        "precio_compra": None,
        "precio_especial": None,
        "precio_umbral": _a_decimal(viejo.get("precio_umbral")),
        "precio_bajo_umbral": _a_decimal(viejo.get("precio_bajo_umbral")),
        "precio_sobre_umbral": _a_decimal(viejo.get("precio_sobre_umbral")),
        "iva": iva,
        "permite_fraccion": tiene_fracciones,
        "activo": True if activo is None else bool(activo),
    }


def mapear_fraccion(vieja: Mapping[str, Any]) -> FraccionMapeada:
    """Mapea una fila de productos_fracciones del viejo al nuevo (sin producto_id). Función PURA.

    `decimal` se deriva parseando el texto de la fracción; `precio_total` cae a 0 si falta
    (columna NOT NULL en el destino).
    """
    precio_total = _a_decimal(vieja.get("precio_total"))
    if precio_total is None:
        precio_total = Decimal("0")
    return {
        "fraccion": vieja.get("fraccion"),
        "decimal": parsear_fraccion(vieja.get("fraccion")),
        "precio_total": precio_total,
        "precio_unitario": _a_decimal(vieja.get("precio_unitario")),
    }


def debe_omitir(
    codigo: str | None,
    nombre: str | None,
    codigos_existentes: set[str],
    nombres_existentes: set[str],
) -> bool:
    """¿El producto ya existe en el destino y debe omitirse? (idempotencia). Función PURA.

    Dedup por `codigo` cuando no es NULL; si `codigo` es NULL, por `nombre`.
    """
    if codigo is not None:
        return codigo in codigos_existentes
    return nombre in nombres_existentes


def _dedup_fracciones(filas: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Quita fracciones repetidas por `fraccion` dentro de un mismo producto (UNIQUE en destino)."""
    vistas: set[Any] = set()
    salida: list[Mapping[str, Any]] = []
    for fila in filas:
        clave = fila.get("fraccion")
        if clave in vistas:
            continue
        vistas.add(clave)
        salida.append(fila)
    return salida


def construir_plan(
    productos: Sequence[Mapping[str, Any]],
    fracciones_por_producto: Mapping[int, Sequence[Mapping[str, Any]]],
) -> Plan:
    """Arma el plan de inserción en memoria (mapeo puro, sin tocar el destino).

    Devuelve [(producto_mapeado, [fracciones_mapeadas]), ...] respetando el orden de `productos`.
    """
    plan: Plan = []
    for prod in productos:
        fracs_origen = _dedup_fracciones(fracciones_por_producto.get(prod["id"], []))
        producto = mapear_producto(prod, tiene_fracciones=bool(fracs_origen))
        fracciones = [mapear_fraccion(f) for f in fracs_origen]
        plan.append((producto, fracciones))
    return plan


def _leer_origen(origen_url: str) -> tuple[list[dict], dict[int, list[dict]]]:
    """Lee productos y sus fracciones del ORIGEN. SOLO SELECT, sesión read-only.

    La sesión se marca READ ONLY: cualquier intento de escritura fallaría (nunca toca el viejo).
    """
    with psycopg.connect(to_libpq(origen_url), row_factory=dict_row) as conn:
        conn.read_only = True
        productos = conn.execute(
            "SELECT id, codigo, nombre, categoria, precio_unidad, unidad_medida, activo, "
            "tiene_iva, porcentaje_iva, precio_umbral, precio_bajo_umbral, precio_sobre_umbral "
            "FROM productos ORDER BY id"
        ).fetchall()
        fracciones = conn.execute(
            "SELECT producto_id, fraccion, precio_total, precio_unitario "
            "FROM productos_fracciones ORDER BY producto_id, fraccion"
        ).fetchall()

    por_producto: dict[int, list[dict]] = {}
    for fila in fracciones:
        por_producto.setdefault(fila["producto_id"], []).append(fila)
    return productos, por_producto


def _reportar_dry_run(plan: Plan) -> None:
    """Imprime el reporte de un dry-run: totales + 3-5 ejemplos del mapeo (no escribe nada)."""
    total = len(plan)
    con_fraccion = sum(1 for _, fracs in plan if fracs)
    fracciones_totales = sum(len(fracs) for _, fracs in plan)
    print(f"[dry-run] productos a procesar: {total}")
    print(f"[dry-run] con fracción: {con_fraccion}; fracciones totales: {fracciones_totales}")
    print("[dry-run] ejemplos de mapeo:")
    for producto, fracciones in plan[:5]:
        print(
            f"  - nombre={producto['nombre']!r} codigo={producto['codigo']!r} "
            f"precio_venta={producto['precio_venta']} iva={producto['iva']} "
            f"permite_fraccion={producto['permite_fraccion']}"
        )
        for fraccion in fracciones[:2]:
            print(
                f"      fracción {fraccion['fraccion']!r} -> decimal={fraccion['decimal']} "
                f"precio_total={fraccion['precio_total']}"
            )


_INSERT_PRODUCTO = (
    "INSERT INTO productos (codigo, nombre, categoria, proveedor_id, unidad_medida, precio_venta, "
    "precio_compra, precio_especial, precio_umbral, precio_bajo_umbral, precio_sobre_umbral, "
    "iva, permite_fraccion, activo) "
    "VALUES (%(codigo)s, %(nombre)s, %(categoria)s, %(proveedor_id)s, %(unidad_medida)s, %(precio_venta)s, "
    "%(precio_compra)s, %(precio_especial)s, %(precio_umbral)s, %(precio_bajo_umbral)s, "
    "%(precio_sobre_umbral)s, %(iva)s, %(permite_fraccion)s, %(activo)s) RETURNING id"
)
_INSERT_INVENTARIO = "INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (%s, 0, 0)"
_INSERT_FRACCION = (
    "INSERT INTO productos_fracciones (producto_id, fraccion, decimal, precio_total, precio_unitario) "
    "VALUES (%s, %s, %s, %s, %s)"
)


def cargar_destino(destino_url: str, plan: Plan) -> dict[str, int]:
    """Inserta el plan en el DESTINO en UNA sola transacción. Idempotente.

    Lee primero los `codigo`/`nombre` ya presentes; los productos que ya existen NO se pisan
    (se omiten). Por cada producto nuevo: inserta el producto, su fila de inventario (stock 0) y
    sus fracciones. Si algo falla, la transacción se revierte entera. Devuelve el resumen.
    """
    insertados = 0
    omitidos = 0
    fracciones_insertadas = 0

    with psycopg.connect(to_libpq(destino_url), row_factory=dict_row) as conn:
        codigos = {
            fila["codigo"]
            for fila in conn.execute("SELECT codigo FROM productos WHERE codigo IS NOT NULL").fetchall()
        }
        nombres = {fila["nombre"] for fila in conn.execute("SELECT nombre FROM productos").fetchall()}

        for producto, fracciones in plan:
            if debe_omitir(producto["codigo"], producto["nombre"], codigos, nombres):
                omitidos += 1
                continue

            producto_id = conn.execute(_INSERT_PRODUCTO, producto).fetchone()["id"]
            conn.execute(_INSERT_INVENTARIO, (producto_id,))
            for fraccion in fracciones:
                conn.execute(
                    _INSERT_FRACCION,
                    (
                        producto_id,
                        fraccion["fraccion"],
                        fraccion["decimal"],
                        fraccion["precio_total"],
                        fraccion["precio_unitario"],
                    ),
                )
                fracciones_insertadas += 1
            insertados += 1

            # Marcar lo recién insertado para que un duplicado dentro del MISMO lote también se omita.
            if producto["codigo"] is not None:
                codigos.add(producto["codigo"])
            nombres.add(producto["nombre"])

        conn.commit()

    return {
        "total": len(plan),
        "insertados": insertados,
        "omitidos": omitidos,
        "fracciones_insertadas": fracciones_insertadas,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ETL de un solo uso: catálogo de productos del FerreBot viejo -> tenant Punto Rojo.",
    )
    parser.add_argument("--origen", required=True, help="URL pública de la base ORIGEN (FerreBot viejo).")
    parser.add_argument("--destino", required=True, help="URL pública de la base DESTINO (tenant PR).")
    parser.add_argument(
        "--dry-run", action="store_true", help="Mapea y reporta sin escribir en el destino.",
    )
    args = parser.parse_args(argv)

    productos, fracciones = _leer_origen(args.origen)
    plan = construir_plan(productos, fracciones)

    if args.dry_run:
        _reportar_dry_run(plan)
        return 0

    resumen = cargar_destino(args.destino, plan)
    print(
        f"productos: total={resumen['total']} insertados={resumen['insertados']} "
        f"omitidos={resumen['omitidos']} (ya existían); "
        f"fracciones insertadas={resumen['fracciones_insertadas']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
