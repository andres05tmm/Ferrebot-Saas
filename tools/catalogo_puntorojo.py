"""Cuadrar el catálogo de Punto Rojo: empaques, duplicados y unidades (issue #179).

La ferretería no llevaba inventario. Al ir a llevarlo aparecieron tres cosas que lo impedían, todas
de DATOS, no de código:

1. **Duplicados de escritura.** 24 tornillos existían dos veces —`GALBANIZADO` y `GALVANIZADO`— más
   otros 3 pares. Se compra en uno y se vende del otro: ninguno cuadra jamás.
2. **Polvos sin empaque.** El cemento, el yeso y el talco se venden por bulto Y por kilo suelto,
   contra el mismo montón. Sin el tamaño y el precio del bulto (0069/0072) solo cabía un precio.
3. **Unidades mal puestas.** El estuco decía "Kg" con el precio de la bolsa; el cuñete de Placco
   decía "Galón".

Todo lo de aquí son respuestas del dueño (2026-07-26), no supuestos. Ver el issue para la tabla
completa y la conversación que la originó.

El script es IDEMPOTENTE: correrlo dos veces deja lo mismo. Los duplicados quedan INACTIVOS, nunca
borrados — los referencian ventas viejas y borrarlos rompería el histórico fiscal.

Uso:
    python -m tools.catalogo_puntorojo --slug puntorojo [--aplicar]     # local
    python -m tools.catalogo_puntorojo --slug puntorojo --prod --aplicar

Sin `--aplicar` hace un ENSAYO: imprime todo lo que haría y no escribe nada.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq


# --------------------------- los datos del dueño ---------------------------

@dataclass(frozen=True)
class Empacado:
    """Un producto que se vende suelto Y por empaque, contra el mismo inventario."""

    nombre: str                 # nombre EXACTO en el catálogo (para encontrarlo)
    contenido: Decimal          # cuántas unidades de venta trae el empaque
    nombre_paquete: str
    precio_paquete: Decimal
    precio_venta: Decimal       # el de UNA unidad suelta
    unidad_medida: str = "Kg"
    renombrar_a: str | None = None
    fracciones: tuple[tuple[str, Decimal, Decimal], ...] = ()   # (etiqueta, decimal, precio_total)


@dataclass(frozen=True)
class PorKilo:
    """Se vende por kilo y fracciones de kilo; no viene en empaque que se venda entero."""

    nombre: str
    precio_venta: Decimal
    fracciones: tuple[tuple[str, Decimal, Decimal], ...] = ()
    renombrar_a: str | None = None


MEDIO_Y_CUARTO = (("1/2", Decimal("0.5"), Decimal("7000")), ("1/4", Decimal("0.25"), Decimal("4000")))
SOLDADURA_FRACCIONES = (
    ("1/2", Decimal("0.5"), Decimal("10000")), ("1/4", Decimal("0.25"), Decimal("5000")),
)

EMPACADOS: tuple[Empacado, ...] = (
    Empacado("Cemente Gris", Decimal("50"), "bulto", Decimal("28000"), Decimal("1500"),
             renombrar_a="Cemento Gris"),
    # Dos marcas, dos proveedores, dos bolsas: son productos DISTINTOS con inventario propio.
    Empacado("Cemento Blanco", Decimal("40"), "bolsa", Decimal("100000"), Decimal("2500"),
             renombrar_a="Cemento Blanco Argos"),
    Empacado("Carbonato x Kg", Decimal("25"), "bolsa", Decimal("26500"), Decimal("2000"),
             renombrar_a="Carbonato"),
    Empacado("Yeso", Decimal("25"), "bolsa", Decimal("50000"), Decimal("2000")),
    Empacado("Talco", Decimal("25"), "bolsa", Decimal("33000"), Decimal("2000")),
    Empacado("Marmolina", Decimal("40"), "bolsa", Decimal("40000"), Decimal("2000")),
    Empacado("Granito N°1", Decimal("40"), "bolsa", Decimal("40000"), Decimal("2000")),
    # Venía marcado en Kg con el precio de la bolsa entera: quien pidiera "1 kg" pagaba $40.000.
    Empacado("Estuco TRIO X 25 Kg", Decimal("25"), "bolsa", Decimal("45000"), Decimal("2000"),
             renombrar_a="Estuco TRIO"),
    # Venía como "Unidad" (la bolsa); ahora se cuenta en kilos y la bolsa es su empaque.
    Empacado("Pegante Ceramico X bolsa", Decimal("25"), "bolsa", Decimal("23000"), Decimal("2000"),
             renombrar_a="Pegante Cerámico"),
    # 12 wayper son un kilo. Se cuenta por UNIDAD (es lo que se agarra con la mano) y el kilo es el
    # empaque; así el conteo físico no obliga a pesar nada.
    Empacado("WAYPER BLANCO", Decimal("12"), "kilo", Decimal("10000"), Decimal("1000"),
             unidad_medida="Unidad", renombrar_a="Wayper Blanco",
             fracciones=(("1/2 kilo", Decimal("6"), Decimal("5000")),)),
    Empacado("WAYPER DE COLOR", Decimal("12"), "kilo", Decimal("8000"), Decimal("700"),
             unidad_medida="Unidad", renombrar_a="Wayper de Color",
             fracciones=(("1/2 kilo", Decimal("6"), Decimal("4000")),)),
)

POR_KILO: tuple[PorKilo, ...] = (
    PorKilo("Acronal", Decimal("13000"), (("1/2", Decimal("0.5"), Decimal("7000")),)),
    PorKilo("Amoniaco sin Olor (Base)", Decimal("13000"), MEDIO_Y_CUARTO),
    PorKilo("Latecol", Decimal("13000"), MEDIO_Y_CUARTO),
    PorKilo("Silicato", Decimal("13000"), MEDIO_Y_CUARTO),
    # El calibre en el nombre: "delgada" no le decía nada a nadie en el mostrador.
    PorKilo("SOLDADURA 60/11", Decimal("19000"), SOLDADURA_FRACCIONES,
            renombrar_a='Soldadura 60/11 1/8"'),
    PorKilo("SOLDADURA 60/11 DELGADA", Decimal("20000"), SOLDADURA_FRACCIONES,
            renombrar_a='Soldadura 60/11 3/32"'),
    PorKilo("Soldadura 60/13", Decimal("19000"), SOLDADURA_FRACCIONES,
            renombrar_a='Soldadura 60/13 1/8"'),
)

# Productos que hay que CREAR (no existían en el catálogo).
NUEVOS_POR_KILO: tuple[PorKilo, ...] = (
    PorKilo('Soldadura 60/13 3/32"', Decimal("20000"), SOLDADURA_FRACCIONES),
)
NUEVOS_EMPACADOS: tuple[Empacado, ...] = (
    Empacado("Cemento Blanco Cemex", Decimal("20"), "bolsa", Decimal("50000"), Decimal("2500")),
)

# Duplicados: (el que se queda, el que se inactiva). El primero es el nombre bueno.
DUPLICADOS: tuple[tuple[str, str], ...] = (
    ("Masilla Mastick", "masillamastick"),
    ("Tornillo Estufa 3/16 X 3", "TORNILLO ESTUFA 3/16X3"),
    ("Tornillo Estufa 3/16 X 2 1/2", "TORNILLO ESTUFA 3/16X 2-1/2"),
    ("Carbonato x Kg", "Carbonato X 25 Kg"),
    ("WAYPER BLANCO", "WAYPER BLANCO UNIDAD"),
    ("WAYPER DE COLOR", "WAYPER DE COLOR UNIDAD"),
)

# Precios que el dueño corrigió sobre productos que se quedan.
PRECIOS_CORREGIDOS: tuple[tuple[str, Decimal], ...] = (
    ("TORNILLOS HEX 1/4X1 GALVANIZADO", Decimal("500")),
)

# Cuñetes y medios cuñetes: se venden ENTEROS, como unidad, sin relación de inventario con el galón
# (son productos distintos, no el mismo líquido en otro envase).
UNIDAD_FORZADA: tuple[str, ...] = ("Placco X Cuñete",)

TYPOS: tuple[tuple[str, str], ...] = (
    ("Anticorrosivo Amarilo", "Anticorrosivo Amarillo"),
    ("Vinilo Davinci T1 Gris Baslto", "Vinilo Davinci T1 Gris Basalto"),
)


# nombre viejo → nombre nuevo, para reconocer lo que este mismo script ya renombró.
_RENOMBRES: dict[str, str] = {
    p.nombre: p.renombrar_a
    for p in (*EMPACADOS, *POR_KILO) if p.renombrar_a
}


# --------------------------- aplicación -----------------------------------

@dataclass
class Reporte:
    """Lo que el script hizo (o haría, en ensayo). Para imprimir y para los tests."""

    inactivados: list[str] = field(default_factory=list)
    empacados: list[str] = field(default_factory=list)
    por_kilo: list[str] = field(default_factory=list)
    creados: list[str] = field(default_factory=list)
    renombrados: list[str] = field(default_factory=list)
    precios: list[str] = field(default_factory=list)
    unidades: list[str] = field(default_factory=list)
    sin_encontrar: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(x) for x in (self.inactivados, self.empacados, self.por_kilo, self.creados,
                                    self.renombrados, self.precios, self.unidades))


def _id_de(conn, *nombres: str | None) -> int | None:
    """Id del producto ACTIVO que coincida con alguno de estos nombres (el primero que exista).

    Acepta varios a propósito: en la segunda corrida el producto ya se llama como quedó renombrado,
    y buscar solo por el nombre viejo haría que el script se reportara a sí mismo como "no
    encontrado". Idempotencia también en lo que informa, no solo en lo que escribe.
    """
    for nombre in nombres:
        if not nombre:
            continue
        fila = conn.execute(
            "SELECT id FROM productos WHERE lower(btrim(nombre)) = lower(btrim(%s)) AND activo "
            "ORDER BY id LIMIT 1",
            (nombre,),
        ).fetchone()
        if fila:
            return fila["id"]
    return None


def _renombrar(conn, pid: int, nuevo: str | None, rep: Reporte) -> None:
    """Renombra y lo reporta SOLO si de verdad cambió: en la segunda corrida no hay nada que contar."""
    if not nuevo:
        return
    actual = conn.execute("SELECT nombre FROM productos WHERE id=%s", (pid,)).fetchone()["nombre"]
    if actual == nuevo:
        return
    conn.execute("UPDATE productos SET nombre=%s WHERE id=%s", (nuevo, pid))
    rep.renombrados.append(f"{actual} → {nuevo}")


def _fracciones(conn, pid: int, filas: tuple[tuple[str, Decimal, Decimal], ...]) -> None:
    """Deja EXACTAMENTE estas fracciones (borra las que sobren). Idempotente."""
    conn.execute("DELETE FROM productos_fracciones WHERE producto_id=%s", (pid,))
    for etiqueta, decimal_, precio in filas:
        conn.execute(
            "INSERT INTO productos_fracciones (producto_id, fraccion, decimal, precio_total, "
            "precio_unitario) VALUES (%s,%s,%s,%s,0)",
            (pid, etiqueta, decimal_, precio),
        )


def _inactivar_duplicados(conn, rep: Reporte) -> None:
    """Los duplicados se INACTIVAN, no se borran: los referencian ventas del histórico fiscal.

    Los 24 pares galbanizado/galvanizado se resuelven por regla (misma medida, distinta ortografía),
    no uno por uno: la lista literal se desactualizaría al primer tornillo nuevo.
    """
    filas = conn.execute(
        "SELECT id, nombre FROM productos WHERE activo AND nombre ILIKE %s ORDER BY id",
        ("%galbaniz%",),
    ).fetchall()
    for fila in filas:
        gemelo = fila["nombre"].upper().replace("GALBANIZ", "GALVANIZ")
        if _id_de(conn, gemelo) is None:
            rep.sin_encontrar.append(f"{fila['nombre']} (no tiene gemelo galvanizado — se deja)")
            continue
        conn.execute("UPDATE productos SET activo=false WHERE id=%s", (fila["id"],))
        rep.inactivados.append(fila["nombre"])

    for queda, se_va in DUPLICADOS:
        pid = _id_de(conn, se_va)
        if pid is None:
            continue    # ya inactivado en una corrida anterior
        # Nunca inactivar el duplicado sin confirmar que el bueno sigue vivo: si el nombre del que
        # se queda cambió, este script dejaría el producto sin ninguna versión activa.
        if _id_de(conn, queda, _RENOMBRES.get(queda)) is None:
            rep.sin_encontrar.append(f"{se_va}: no se inactiva porque no encuentro '{queda}'")
            continue
        conn.execute("UPDATE productos SET activo=false WHERE id=%s", (pid,))
        rep.inactivados.append(se_va)


def _aplicar_empacado(conn, emp: Empacado, rep: Reporte) -> None:
    pid = _id_de(conn, emp.nombre, emp.renombrar_a)
    if pid is None:
        rep.sin_encontrar.append(emp.nombre)
        return
    conn.execute(
        "UPDATE productos SET unidad_medida=%s, precio_venta=%s, precio_paquete=%s, "
        "contenido_paquete=%s, nombre_paquete=%s WHERE id=%s",
        (emp.unidad_medida, emp.precio_venta, emp.precio_paquete, emp.contenido,
         emp.nombre_paquete, pid),
    )
    _fracciones(conn, pid, emp.fracciones)
    _renombrar(conn, pid, emp.renombrar_a, rep)
    rep.empacados.append(emp.renombrar_a or emp.nombre)


def _aplicar_por_kilo(conn, prod: PorKilo, rep: Reporte) -> None:
    pid = _id_de(conn, prod.nombre, prod.renombrar_a)
    if pid is None:
        rep.sin_encontrar.append(prod.nombre)
        return
    conn.execute(
        "UPDATE productos SET unidad_medida='Kg', precio_venta=%s, permite_fraccion=%s, "
        "precio_paquete=NULL, contenido_paquete=NULL, nombre_paquete=NULL WHERE id=%s",
        (prod.precio_venta, bool(prod.fracciones), pid),
    )
    _fracciones(conn, pid, prod.fracciones)
    _renombrar(conn, pid, prod.renombrar_a, rep)
    rep.por_kilo.append(prod.renombrar_a or prod.nombre)


def _crear(conn, nombre: str, *, unidad: str, precio: Decimal, iva: int) -> int:
    pid = conn.execute(
        "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
        "VALUES (%s,%s,%s,%s,false,true) RETURNING id",
        (nombre, unidad, precio, iva),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (%s,0,0) "
        "ON CONFLICT (producto_id) DO NOTHING",
        (pid,),
    )
    return pid


def aplicar(conn, *, iva: int = 19) -> Reporte:
    """Aplica todo el cuadre sobre la conexión dada. Idempotente; no hace commit (lo hace el caller)."""
    rep = Reporte()
    _inactivar_duplicados(conn, rep)

    for nuevo in NUEVOS_EMPACADOS:
        if _id_de(conn, nuevo.nombre) is None:
            _crear(conn, nuevo.nombre, unidad=nuevo.unidad_medida, precio=nuevo.precio_venta, iva=iva)
            rep.creados.append(nuevo.nombre)
    for nuevo_k in NUEVOS_POR_KILO:
        if _id_de(conn, nuevo_k.nombre) is None:
            _crear(conn, nuevo_k.nombre, unidad="Kg", precio=nuevo_k.precio_venta, iva=iva)
            rep.creados.append(nuevo_k.nombre)

    for emp in (*EMPACADOS, *NUEVOS_EMPACADOS):
        _aplicar_empacado(conn, emp, rep)
    for prod in (*POR_KILO, *NUEVOS_POR_KILO):
        _aplicar_por_kilo(conn, prod, rep)

    for nombre, precio in PRECIOS_CORREGIDOS:
        pid = _id_de(conn, nombre)
        if pid is None:
            rep.sin_encontrar.append(nombre)
            continue
        conn.execute("UPDATE productos SET precio_venta=%s WHERE id=%s", (precio, pid))
        rep.precios.append(f"{nombre} = {precio}")

    for nombre in UNIDAD_FORZADA:
        pid = _id_de(conn, nombre)
        if pid is None:
            rep.sin_encontrar.append(nombre)
            continue
        conn.execute("UPDATE productos SET unidad_medida='Unidad' WHERE id=%s", (pid,))
        rep.unidades.append(nombre)

    # Una sola forma de escribir la unidad: 'Gramos' y 'GRM' son lo mismo y parten el catálogo en dos.
    afectados = conn.execute(
        "UPDATE productos SET unidad_medida='GRM' WHERE lower(btrim(unidad_medida))='gramos' "
        "RETURNING nombre"
    ).fetchall()
    rep.unidades.extend(f"{f['nombre']} (Gramos → GRM)" for f in afectados)

    for malo, bueno in TYPOS:
        pid = _id_de(conn, malo)
        if pid is not None:
            _renombrar(conn, pid, bueno, rep)
    return rep


# --------------------------- CLI ------------------------------------------

def _url_tenant(slug: str) -> str:
    settings = get_settings()
    with psycopg.connect(to_libpq(settings.control_database_url), row_factory=dict_row) as conn:
        fila = conn.execute(
            "SELECT t.db_name FROM empresas e JOIN tenant_databases t ON t.empresa_id = e.id "
            "WHERE e.slug = %s",
            (slug,),
        ).fetchone()
    if fila is None:
        raise ValueError(f"empresa '{slug}' no existe o no tiene base")
    # Conexión DIRECTA (no por PgBouncer): es un script de operación, no tráfico de la app.
    return tenant_url(settings.tenants_direct_url_base, fila["db_name"])


def _imprimir(rep: Reporte, *, ensayo: bool) -> None:
    print("ENSAYO (no se escribió nada)" if ensayo else "APLICADO")
    for titulo, filas in (
        ("Inactivados (duplicados)", rep.inactivados),
        ("Creados", rep.creados),
        ("Con empaque", rep.empacados),
        ("Por kilo", rep.por_kilo),
        ("Renombrados", rep.renombrados),
        ("Precios corregidos", rep.precios),
        ("Unidades corregidas", rep.unidades),
    ):
        if filas:
            print(f"\n{titulo} ({len(filas)}):")
            for f in filas:
                print(f"  · {f}")
    if rep.sin_encontrar:
        print(f"\n⚠ No encontrados ({len(rep.sin_encontrar)}) — revisar a mano:")
        for f in rep.sin_encontrar:
            print(f"  · {f}")
    print(f"\nTotal de cambios: {rep.total}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cuadrar el catálogo de Punto Rojo (issue #179).")
    parser.add_argument("--slug", default="puntorojo")
    parser.add_argument("--prod", action="store_true", help="correr contra producción (.env.prod)")
    parser.add_argument("--aplicar", action="store_true", help="escribir (sin esto es un ensayo)")
    parser.add_argument("--iva", type=int, default=19, help="IVA de los productos nuevos")
    args = parser.parse_args(argv)

    if args.prod:
        from tools._prodenv import cargar_env_prod
        print(f"· entorno: {cargar_env_prod()}")

    with psycopg.connect(to_libpq(_url_tenant(args.slug)), row_factory=dict_row) as conn:
        reporte = aplicar(conn, iva=args.iva)
        if args.aplicar:
            conn.commit()
        else:
            conn.rollback()
    _imprimir(reporte, ensayo=not args.aplicar)
    if not args.aplicar:
        print("\nCorre con --aplicar para escribirlo.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
