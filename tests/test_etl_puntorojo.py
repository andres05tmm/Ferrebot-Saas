"""Tests del ETL FerreBot → tenant Punto Rojo (`tools/etl_puntorojo/`).

Invariantes críticos (TDD, `.claude/rules/testing.md`):
  - **Idempotencia:** correr `cargar` dos veces con el mismo origen deja los mismos conteos
    (cero duplicados, incluido el histórico fiscal).
  - **Stock solo con kardex:** todo producto migrado con stock>0 queda con su movimiento
    AJUSTE `referencia='migracion'`.

Además: tests unitarios de las transformaciones puras (dinero, fechas frontera, split del
numero FE, renumeración de consecutivos) y el preflight contra datos ajenos en el destino.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from core.db.urls import to_libpq
from tools.etl_puntorojo import transform
from tools.etl_puntorojo.load import DestinoNoVacioError, cargar
from tools.etl_puntorojo.sequences import ajustar_secuencias

# ---------------------------------------------------------------- transform (puras)


def test_dinero_int_a_numeric_sin_dividir():
    assert transform.dinero(11900) == Decimal("11900")
    assert transform.dinero(None) is None


def test_combinar_fecha_hora_frontera_7pm():
    """Venta de las 7 PM Colombia cae el MISMO día al volverla a leer en hora Colombia."""
    ts = transform.combinar_fecha_hora(date(2026, 6, 16), time(19, 0))
    assert ts.tzinfo is not None
    assert ts.astimezone(timezone.utc).hour == 0          # 19:00-05 = 00:00 UTC del día siguiente
    assert ts.astimezone(transform.CO).date() == date(2026, 6, 16)


def test_combinar_fecha_hora_sin_hora_usa_mediodia():
    """Sin hora: mediodía Colombia (no medianoche, que cruzaría de día en UTC)."""
    ts = transform.combinar_fecha_hora(date(2026, 6, 16), None)
    assert ts.astimezone(transform.CO).date() == date(2026, 6, 16)


def test_utc_naive_a_aware():
    """created_at naive del servidor legacy (Etc/UTC) se marca UTC sin desplazar."""
    ts = transform.utc_naive_a_aware(datetime(2026, 6, 16, 12, 53, 5))
    assert ts == datetime(2026, 6, 16, 12, 53, 5, tzinfo=timezone.utc)


def test_split_numero_fe():
    assert transform.split_numero_fe("FPR100") == ("FPR", 100)
    assert transform.split_numero_fe("ERR-53") == ("ERR", 53)
    assert transform.split_numero_fe("FE-1234") == ("FE", 1234)


def test_fraccion_a_decimal():
    assert transform.fraccion_a_decimal("1/2") == Decimal("0.5")
    assert transform.fraccion_a_decimal("1/4") == Decimal("0.25")
    assert transform.fraccion_a_decimal("3/4") == Decimal("0.75")
    assert transform.fraccion_a_decimal("no-fraccion") is None


def test_ventas_renumera_consecutivo_global():
    """El consecutivo legacy se reinicia por día (UNIQUE global en destino): se renumera
    en orden cronológico (fecha, hora, id) y queda 1..N sin huecos ni duplicados."""
    usuarios = [{"id": 1, "nombre": "Admin", "rol": "admin"}, {"id": 2, "nombre": "Pedro", "rol": "vendedor"}]
    rows = [
        {"id": 10, "consecutivo": 1, "fecha": date(2026, 6, 2), "hora": time(9, 0), "cliente_id": None,
         "usuario_id": 2, "vendedor": "Pedro", "metodo_pago": "efectivo", "total": 5000, "created_at": None},
        {"id": 11, "consecutivo": 2, "fecha": date(2026, 6, 2), "hora": time(10, 0), "cliente_id": None,
         "usuario_id": None, "vendedor": "Pedro", "metodo_pago": "datafono", "total": 8000, "created_at": None},
        {"id": 9, "consecutivo": 1, "fecha": date(2026, 6, 1), "hora": time(15, 0), "cliente_id": None,
         "usuario_id": None, "vendedor": None, "metodo_pago": None, "total": 3000, "created_at": None},
    ]
    salida = transform.t_ventas(rows, usuarios=usuarios)
    por_id = {v["id"]: v for v in salida}
    assert [v["consecutivo"] for v in sorted(salida, key=lambda v: v["fecha"])] == [1, 2, 3]
    assert len({v["consecutivo"] for v in salida}) == 3
    assert por_id[11]["vendedor_id"] == 2          # resuelto por texto 'Pedro'
    assert por_id[9]["vendedor_id"] == 1           # fallback: primer admin
    assert por_id[9]["metodo_pago"] == "efectivo"  # fallback de método
    assert por_id[11]["metodo_pago"] == "datafono" # se preserva (existe en el enum destino)
    assert por_id[10]["subtotal"] == Decimal("5000") and por_id[10]["impuestos"] == Decimal("0")


def test_productos_codigo_fallback_y_dedupe():
    fila_sin_codigo = {"id": 1, "clave": "MART01", "nombre": "Martillo", "codigo": None, "categoria": None,
                       "unidad_medida": "Unidad", "precio_unidad": 11900, "tiene_iva": True, "porcentaje_iva": 19,
                       "precio_umbral": None, "precio_bajo_umbral": None, "precio_sobre_umbral": None,
                       "activo": True, "created_at": None, "updated_at": None}
    fila_dup = dict(fila_sin_codigo, id=2, clave="MART02", nombre="Martillo 2", codigo="X1")
    fila_dup2 = dict(fila_sin_codigo, id=3, clave="MART03", nombre="Martillo 3", codigo="X1")
    vistos: set[str] = set()
    p1 = transform.t_producto(fila_sin_codigo, fracciones_ids={1}, codigos_vistos=vistos)
    p2 = transform.t_producto(fila_dup, fracciones_ids=set(), codigos_vistos=vistos)
    p3 = transform.t_producto(fila_dup2, fracciones_ids=set(), codigos_vistos=vistos)
    assert p1["codigo"] == "MART01" and p1["permite_fraccion"] is True and p1["iva"] == 19
    assert p2["codigo"] == "X1"
    assert p3["codigo"] != "X1" and p3["codigo"]   # dedupe: sufijo estable
    assert p1["precio_venta"] == Decimal("11900")


def test_gasto_categoria_libre_mapea_a_otros():
    row = {"id": 1, "fecha": date(2026, 6, 2), "hora": time(9, 0), "concepto": "Almuerzo", "monto": 15000,
           "categoria": "Alimentación", "fac_id": None, "usuario_id": 2, "created_at": None}
    g = transform.t_gasto(row)
    assert g["categoria"] == "otros"
    assert g["monto"] == Decimal("15000")


def test_factura_electronica_estados_y_split():
    row = {"id": 5, "venta_id": 10, "numero": "FPR100", "cufe": "abc123", "estado": "emitida",
           "tipo": "factura", "cliente_nombre": "Juan", "total": 20000, "error_msg": None,
           "razon_id": None, "factura_cufe_ref": None,
           "fecha_emision": datetime(2026, 6, 2, 15, 0), "created_at": datetime(2026, 6, 2, 15, 0)}
    fe = transform.t_factura_electronica(row)
    assert fe["prefijo"] == "FPR" and fe["consecutivo"] == 100
    assert fe["estado"] == "aceptada" and fe["cufe"] == "abc123"
    assert fe["dian_respuesta"]["numero_original"] == "FPR100"
    fe_err = transform.t_factura_electronica(dict(row, numero="ERR-53", estado="error", error_msg="boom"))
    assert fe_err["estado"] == "error" and fe_err["dian_respuesta"]["error_msg"] == "boom"


# ---------------------------------------------------------------- origen mínimo compartido


def _origen_minimo() -> dict[str, list[dict]]:
    """Origen legacy sintético con las formas reales de FerreBot (subset representativo)."""
    return {
        "usuarios": [
            {"id": 1, "telegram_id": 111, "nombre": "Dueño", "rol": "admin", "activo": True,
             "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            {"id": 2, "telegram_id": 222, "nombre": "Pedro", "rol": "vendedor", "activo": True,
             "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        ],
        "productos": [
            {"id": 1, "clave": "MART01", "nombre": "Martillo", "codigo": "C1", "categoria": "Herramienta",
             "unidad_medida": "Unidad", "precio_unidad": 11900, "tiene_iva": True, "porcentaje_iva": 19,
             "precio_umbral": None, "precio_bajo_umbral": None, "precio_sobre_umbral": None,
             "aliases": ["martiyo"], "activo": True,
             "created_at": datetime(2025, 1, 1, 12, 0), "updated_at": None},
            {"id": 2, "clave": "VIN01", "nombre": "Vinilo T1 Blanco", "codigo": None, "categoria": "Pintura",
             "unidad_medida": "Galon", "precio_unidad": 45000, "tiene_iva": True, "porcentaje_iva": 19,
             "precio_umbral": 10, "precio_bajo_umbral": 45000, "precio_sobre_umbral": 40000,
             "aliases": [], "activo": True,
             "created_at": datetime(2025, 1, 1, 12, 0), "updated_at": None},
        ],
        "productos_fracciones": [
            {"id": 1, "producto_id": 2, "fraccion": "1/2", "precio_total": 25000, "precio_unitario": 50000},
        ],
        "aliases": [{"termino": "tiner", "reemplazo": "thinner", "created_at": None, "updated_at": None}],
        "clientes": [
            {"id": 1, "nombre": "Juan Pérez", "tipo_id": "CC", "identificacion": "123", "tipo_persona": None,
             "correo": None, "telefono": "300", "direccion": None, "regimen_fiscal": 2,
             "municipio_dian": 149, "pais_id": 45, "ciudad_nombre": "Cartagena",
             "created_at": datetime(2025, 2, 1, 12, 0)},
        ],
        "inventario": [
            {"producto_id": 1, "cantidad": Decimal("10"), "minimo": Decimal("2"),
             "ultimo_costo": Decimal("8000"), "updated_at": None},
        ],
        "ventas": [
            {"id": 1, "consecutivo": 1, "fecha": date(2026, 6, 1), "hora": time(9, 30), "cliente_id": 1,
             "usuario_id": 2, "vendedor": "Pedro", "metodo_pago": "efectivo", "total": 11900,
             "created_at": datetime(2026, 6, 1, 14, 30)},
            {"id": 2, "consecutivo": 1, "fecha": date(2026, 6, 2), "hora": time(19, 0), "cliente_id": None,
             "usuario_id": None, "vendedor": "Pedro", "metodo_pago": "datafono", "total": 45000,
             "created_at": datetime(2026, 6, 3, 0, 0)},
        ],
        "ventas_detalle": [
            {"id": 1, "venta_id": 1, "producto_id": 1, "producto_nombre": "Martillo",
             "cantidad": Decimal("1"), "precio_unitario": 11900, "total": 11900,
             "unidad_medida": "Unidad", "alias_usado": None, "sin_detalle": False},
            {"id": 2, "venta_id": 2, "producto_id": 2, "producto_nombre": "Vinilo T1 Blanco",
             "cantidad": Decimal("1"), "precio_unitario": 45000, "total": 45000,
             "unidad_medida": "Galon", "alias_usado": None, "sin_detalle": False},
        ],
        "historico_ventas": [
            {"fecha": date(2026, 5, 31), "ventas": 100000, "efectivo": 60000, "transferencia": 40000,
             "datafono": 0, "n_transacciones": 5, "gastos": 10000, "abonos_proveedores": 0,
             "origen": "calculado", "incluir_en_balances": True, "notas": None, "updated_at": None},
        ],
        "facturas_electronicas": [
            {"id": 1, "venta_id": 1, "numero": "FPR100", "cufe": "cufe-real-1", "estado": "emitida",
             "tipo": "factura", "cliente_nombre": "Juan Pérez", "total": 11900, "error_msg": None,
             "razon_id": None, "factura_cufe_ref": None,
             "fecha_emision": datetime(2026, 6, 1, 14, 35), "created_at": datetime(2026, 6, 1, 14, 35)},
        ],
        "cuentas_cobro": [
            {"id": 1, "consecutivo": 3, "numero_display": "CC-3", "fecha": date(2026, 5, 23),
             "periodo": "2026-05", "concepto": "Honorarios", "valor": Decimal("1000000"),
             "pdf_bytes": None, "enviado_telegram": True,
             "creado_at": datetime(2026, 5, 23, tzinfo=timezone.utc)},
        ],
        "documentos_soporte": [
            {"id": 1, "consecutivo": "DS-3", "fecha": date(2026, 5, 23), "valor": Decimal("1000000"),
             "cude": "cude-real-1", "estado_dian": "aceptado", "cuenta_cobro_id": 1,
             "created_at": datetime(2026, 5, 23, tzinfo=timezone.utc)},
        ],
        "compras_fiscal": [
            {"id": 1, "fecha": date(2026, 5, 20), "hora": time(10, 0), "proveedor": "Ferretería Mayorista",
             "producto_id": 1, "producto_nombre": "Martillo", "cantidad": Decimal("5"),
             "costo_unitario": 8000, "costo_total": 40000, "incluye_iva": True, "tarifa_iva": 19,
             "numero_factura": "FMA-1", "notas_fiscales": None, "compra_origen_id": None, "usuario_id": 1,
             "gmail_message_id": "g1", "cufe_proveedor": "cufe-prov-1",
             "evento_030_at": None, "evento_031_at": None, "evento_032_at": None, "evento_033_at": None,
             "evento_estado": "pendiente", "evento_error": None, "estado_vinculacion": "sin_vincular",
             "created_at": datetime(2026, 5, 20, 15, 0), "updated_at": None},
        ],
        "compras": [],
        "facturas_proveedores": [
            {"id": "FMA-1", "proveedor": "Ferretería Mayorista", "descripcion": "Compra mayo",
             "total": 40000, "pagado": 10000, "pendiente": 30000, "estado": "pendiente",
             "fecha": date(2026, 5, 20), "foto_url": "", "foto_nombre": "", "usuario_id": 1,
             "created_at": datetime(2026, 5, 20, 15, 0)},
        ],
        "facturas_abonos": [
            {"id": 1, "factura_id": "FMA-1", "monto": 10000, "fecha": date(2026, 5, 25),
             "foto_url": "", "foto_nombre": "", "created_at": datetime(2026, 5, 25, 15, 0)},
        ],
        "gastos": [
            {"id": 1, "fecha": date(2026, 6, 1), "hora": time(12, 0), "concepto": "Almuerzo",
             "monto": 15000, "categoria": "Alimentación", "fac_id": None, "usuario_id": 2,
             "created_at": datetime(2026, 6, 1, 17, 0)},
        ],
        "caja": [
            {"id": 1, "fecha": date(2026, 6, 2), "abierta": True, "monto_apertura": 50000,
             "efectivo": 0, "transferencias": 0, "datafono": 0, "cerrada_at": None,
             "created_at": datetime(2026, 6, 2, 13, 0)},
            {"id": 2, "fecha": date(2026, 6, 1), "abierta": False, "monto_apertura": 50000,
             "efectivo": 11900, "transferencias": 0, "datafono": 0,
             "cerrada_at": datetime(2026, 6, 1, 23, 0), "created_at": datetime(2026, 6, 1, 13, 0)},
        ],
        "fiados": [],
        "fiados_movimientos": [],
        "bancolombia_transferencias": [
            {"id": 1, "gmail_message_id": "gm-1", "fecha": date(2026, 6, 1), "hora": "14:02",
             "monto": 20000, "remitente": "MARIA LOPEZ", "descripcion": "", "tipo_transaccion": "Nequi",
             "referencia": "", "notificado": True,
             "created_at": datetime(2026, 6, 1, 19, 2, tzinfo=timezone.utc)},
        ],
        "iva_saldos_bimestrales": [],
        "memoria_entidades": [
            {"id": 1, "tipo": "cliente", "entidad_key": "juan", "nota": "compra los lunes",
             "confidence": 0.9, "fecha_generada": date(2026, 5, 1), "vigente": True,
             "creado_en": datetime(2026, 5, 1, tzinfo=timezone.utc)},
        ],
    }


def _conteo(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


def _conectar(tdb):
    return psycopg.connect(to_libpq(tdb.url), row_factory=dict_row)


# ---------------------------------------------------------------- invariantes (integración)


@pytest.mark.anyio
async def test_etl_idempotente_correr_dos_veces_no_duplica(tenant):
    """INVARIANTE: dos corridas del ETL con el mismo origen → mismos conteos, cero duplicados."""
    origen = _origen_minimo()
    datos = transform.transformar(origen)

    reporte1 = cargar(tenant.url, datos)
    with _conectar(tenant) as conn:
        conteos1 = {t: _conteo(conn, t) for t in
                    ("productos", "ventas", "ventas_detalle", "facturas_electronicas",
                     "clientes", "bancolombia_transferencias", "movimientos_inventario",
                     "gastos", "facturas_proveedores", "facturas_abonos", "aliases")}

    cargar(tenant.url, datos)  # segunda corrida
    with _conectar(tenant) as conn:
        conteos2 = {t: _conteo(conn, t) for t in conteos1}

    assert conteos1 == conteos2
    assert conteos1["ventas"] == 2
    assert conteos1["facturas_electronicas"] == 1          # histórico fiscal intacto
    assert reporte1["ventas"].insertadas == 2


@pytest.mark.anyio
async def test_etl_stock_migrado_deja_movimiento_ajuste(tenant):
    """INVARIANTE: nada tiene stock sin movimiento de inventario — el saldo inicial migrado
    queda documentado con un AJUSTE `referencia='migracion'` por producto."""
    datos = transform.transformar(_origen_minimo())
    cargar(tenant.url, datos)
    with _conectar(tenant) as conn:
        filas = conn.execute(
            "SELECT i.producto_id, i.stock_actual, m.tipo, m.cantidad, m.referencia "
            "FROM inventario i LEFT JOIN movimientos_inventario m ON m.producto_id = i.producto_id "
            "WHERE i.stock_actual > 0"
        ).fetchall()
    assert filas, "el origen tiene stock>0: debe migrar"
    for f in filas:
        assert f["tipo"] == "AJUSTE" and f["referencia"] == "migracion"
        assert f["cantidad"] == f["stock_actual"]


@pytest.mark.anyio
async def test_etl_preflight_rechaza_destino_con_datos_ajenos(tenant):
    """Si el destino tiene filas con PK que NO están en el origen (p.ej. seeds del manifiesto),
    cargar sin `limpiar` aborta; con `limpiar=True` barre las tablas del ETL y carga."""
    with _conectar(tenant) as conn:
        conn.execute("INSERT INTO productos (id, nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                     "VALUES (999, 'Seed ajeno', 'unidad', 100, 0, false, true)")
        conn.commit()
    datos = transform.transformar(_origen_minimo())
    with pytest.raises(DestinoNoVacioError):
        cargar(tenant.url, datos)
    cargar(tenant.url, datos, limpiar=True)
    with _conectar(tenant) as conn:
        assert _conteo(conn, "productos") == 2
        assert conn.execute("SELECT count(*) AS n FROM productos WHERE id=999").fetchone()["n"] == 0


@pytest.mark.anyio
async def test_etl_secuencias_avanzan_tras_carga(tenant):
    """Tras la carga, un INSERT nuevo no choca con los IDs preservados y el consecutivo de
    venta continúa desde el máximo migrado."""
    datos = transform.transformar(_origen_minimo())
    cargar(tenant.url, datos)
    ajustar_secuencias(tenant.url)
    with _conectar(tenant) as conn:
        nuevo = conn.execute(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
            "VALUES ('Nuevo', 'unidad', 100, 0, false, true) RETURNING id"
        ).fetchone()["id"]
        assert nuevo > 2
        consec = conn.execute("SELECT nextval('ventas_consecutivo_seq') AS v").fetchone()["v"]
        assert consec == 3      # 2 ventas migradas → siguiente = 3


@pytest.mark.anyio
async def test_etl_fechas_quedan_en_dia_colombia_correcto(tenant):
    """G4/G5: la venta de las 7 PM Colombia queda el día correcto (no el siguiente)."""
    datos = transform.transformar(_origen_minimo())
    cargar(tenant.url, datos)
    with _conectar(tenant) as conn:
        conn.execute("SET TIME ZONE 'America/Bogota'")
        fila = conn.execute("SELECT fecha::date AS d FROM ventas WHERE id=2").fetchone()
    assert fila["d"] == date(2026, 6, 2)
