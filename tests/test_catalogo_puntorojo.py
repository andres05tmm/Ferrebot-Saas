"""El cuadre del catálogo de Punto Rojo (tools.catalogo_puntorojo, issue #179).

Lo que se prueba es lo que puede salir mal en una corrida contra PRODUCCIÓN: que el duplicado que
se inactiva no sea el bueno, que el bulto quede con su tamaño y su precio, y que correrlo dos veces
no deshaga nada. El resto (la tabla de precios) son datos del dueño, no lógica.
"""
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from core.db.urls import to_libpq
from tools.catalogo_puntorojo import aplicar


def _conn(tenant):
    return psycopg.connect(to_libpq(tenant.url), row_factory=dict_row)


def _sembrar(conn) -> None:
    """Un pedazo del catálogo real: el par galbanizado/galvanizado y los polvos que se menudean."""
    for nombre, unidad, precio in (
        ("TORNILLOS HEX 3/8X2 GALBANIZADO", "Unidad", 700),
        ("TORNILLOS HEX 3/8X2 GALVANIZADO", "Unidad", 700),
        ("TORNILLOS HEX 1/4X1 GALVANIZADO", "Unidad", 400),
        ("Cemente Gris", "Kg", 1500),
        ("Cemento Blanco", "Kg", 2500),
        ("Carbonato x Kg", "Kg", 2000),
        ("Carbonato X 25 Kg", "Unidad", 26000),
        ("Estuco TRIO X 25 Kg", "Kg", 40000),
        ("WAYPER BLANCO", "Kg", 10000),
        ("WAYPER BLANCO UNIDAD", "Unidad", 700),
        ("Silicato", "Kg", 10000),
        ("Placco X Cuñete", "Galón", 150000),
        ('PUNTILLA 1-1/2" CON CABEZA', "Gramos", 10),
    ):
        conn.execute(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
            "activo) VALUES (%s,%s,%s,19,false,true)",
            (nombre, unidad, precio),
        )


def _producto(conn, nombre: str) -> dict | None:
    return conn.execute(
        "SELECT * FROM productos WHERE lower(btrim(nombre))=lower(btrim(%s))", (nombre,)
    ).fetchone()


def test_el_duplicado_que_se_va_es_el_mal_escrito(tenant):
    """Queda el GALVANIZADO (el que existe siempre); el GALBANIZADO se INACTIVA, no se borra."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        galb = _producto(conn, "TORNILLOS HEX 3/8X2 GALBANIZADO")
        galv = _producto(conn, "TORNILLOS HEX 3/8X2 GALVANIZADO")
        assert galb is not None and galb["activo"] is False   # sigue existiendo: lo citan ventas
        assert galv["activo"] is True
        # Y el precio que el dueño corrigió sobre el que se queda.
        assert _producto(conn, "TORNILLOS HEX 1/4X1 GALVANIZADO")["precio_venta"] == Decimal("500.00")
        conn.rollback()


def test_un_galbanizado_sin_gemelo_no_se_toca(tenant):
    """Si no hay galvanizado del mismo calibre no es un duplicado: inactivarlo perdería el producto."""
    with _conn(tenant) as conn:
        conn.execute(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, "
            "activo) VALUES ('TORNILLOS HEX 9/16X9 GALBANIZADO','Unidad',900,19,false,true)"
        )
        reporte = aplicar(conn)

        assert _producto(conn, "TORNILLOS HEX 9/16X9 GALBANIZADO")["activo"] is True
        assert any("9/16X9" in x for x in reporte.sin_encontrar)
        conn.rollback()


def test_el_bulto_queda_con_su_tamano_y_su_precio(tenant):
    """Cemento: kilo suelto $1.500 y bulto de 50 kg a $28.000, sobre el MISMO producto."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        cemento = _producto(conn, "Cemento Gris")          # y de paso corrige el typo "Cemente"
        assert cemento["precio_venta"] == Decimal("1500.00")
        assert cemento["precio_paquete"] == Decimal("28000.00")
        assert cemento["contenido_paquete"] == Decimal("50.000")
        assert cemento["nombre_paquete"] == "bulto"
        conn.rollback()


def test_el_carbonato_deja_de_estar_dos_veces(tenant):
    """La bolsa dejó de ser un producto aparte: es el empaque del mismo kilo."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        assert _producto(conn, "Carbonato X 25 Kg")["activo"] is False
        carbonato = _producto(conn, "Carbonato")
        assert carbonato["activo"] is True
        assert carbonato["contenido_paquete"] == Decimal("25.000")
        assert carbonato["precio_paquete"] == Decimal("26500.00")
        conn.rollback()


def test_las_unidades_mal_puestas_quedan_derechas(tenant):
    """El estuco decía Kg con el precio de la bolsa; el cuñete decía Galón; 'Gramos' y 'GRM' eran dos."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        estuco = _producto(conn, "Estuco TRIO")
        assert estuco["precio_venta"] == Decimal("2000.00")        # el KILO, no la bolsa
        assert estuco["precio_paquete"] == Decimal("45000.00")
        assert _producto(conn, "Placco X Cuñete")["unidad_medida"] == "Unidad"
        assert _producto(conn, 'PUNTILLA 1-1/2" CON CABEZA')["unidad_medida"] == "GRM"
        conn.rollback()


def test_el_wayper_se_cuenta_por_unidad_y_el_kilo_es_su_empaque(tenant):
    """12 wayper son un kilo: se cuenta lo que se agarra con la mano, sin pesar nada."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        wayper = _producto(conn, "Wayper Blanco")
        assert wayper["unidad_medida"] == "Unidad"
        assert wayper["precio_venta"] == Decimal("1000.00")
        assert wayper["contenido_paquete"] == Decimal("12.000")
        assert wayper["precio_paquete"] == Decimal("10000.00")
        assert _producto(conn, "WAYPER BLANCO UNIDAD")["activo"] is False
        conn.rollback()


def test_correrlo_dos_veces_deja_lo_mismo(tenant):
    """Idempotencia: la segunda corrida no inactiva, no crea y no renombra nada."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)
        estado = conn.execute(
            "SELECT nombre, activo, precio_venta, precio_paquete, contenido_paquete "
            "FROM productos ORDER BY nombre"
        ).fetchall()

        segunda = aplicar(conn)

        assert segunda.inactivados == [] and segunda.creados == [] and segunda.renombrados == []
        assert conn.execute(
            "SELECT nombre, activo, precio_venta, precio_paquete, contenido_paquete "
            "FROM productos ORDER BY nombre"
        ).fetchall() == estado
        conn.rollback()


def test_las_fracciones_quedan_en_la_tabla_de_fracciones(tenant):
    """Silicato: el kilo sube a $13.000 y aparecen el ½ y el ¼ que el dueño cobra."""
    with _conn(tenant) as conn:
        _sembrar(conn)
        aplicar(conn)

        pid = _producto(conn, "Silicato")["id"]
        filas = conn.execute(
            "SELECT fraccion, decimal, precio_total FROM productos_fracciones "
            "WHERE producto_id=%s ORDER BY decimal",
            (pid,),
        ).fetchall()
        assert _producto(conn, "Silicato")["precio_venta"] == Decimal("13000.00")
        assert [(f["fraccion"], f["precio_total"]) for f in filas] == [
            ("1/4", Decimal("4000.00")), ("1/2", Decimal("7000.00")),
        ]
        conn.rollback()
