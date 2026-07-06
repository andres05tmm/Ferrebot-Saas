"""Transformaciones puras FerreBot → esquema del tenant (G2-G7 + T-* de decisiones-migracion.md §3-4).

Todas las funciones son puras (dict origen → dict destino); `transformar` orquesta el conjunto y
devuelve un mapa {tabla_destino: [filas destino]} listo para `load.cargar`.

Decisiones tomadas con datos reales (muestreo de la réplica, 2026-07-05):
- **G4 mixto:** `fecha`+`hora` las escribió la app en hora Colombia; los `created_at` naive los
  escribió el servidor en Etc/UTC (delta exacto de −5h en la muestra). Por eso hay dos reglas:
  `combinar_fecha_hora` (localiza America/Bogota) y `utc_naive_a_aware` (marca UTC sin desplazar).
- **Consecutivo de venta:** en FerreBot se reinicia por día; el destino tiene UNIQUE global.
  Se renumera 1..N en orden cronológico (fecha, hora, id). No es un número legal (lo legal es
  el `numero` de la factura electrónica, que sí se preserva).
- **`metodo_pago` `datafono` se preserva** (existe en el enum destino; el mapeo a `tarjeta` del
  doc era para un enum anterior).
- **`numero` FE:** formato real `FPR<n>` (prefijo sin guion) y `ERR-<n>` para emisiones fallidas.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

CO = ZoneInfo("America/Bogota")

_RE_NUMERO_FE = re.compile(r"^([A-Za-z]+)-?(\d+)$")
_RE_FRACCION = re.compile(r"^(\d+)\s*/\s*(\d+)$")

# Categorías de gasto del enum destino; todo lo demás cae a 'otros'.
_GASTO_CATEGORIAS = {"transporte", "papeleria", "servicios", "nomina", "mantenimiento", "otros"}

_FE_ESTADO = {"emitida": "aceptada", "error": "error", "pendiente": "pendiente",
              "enviada": "enviada", "aceptada": "aceptada", "rechazada": "rechazada",
              "anulada": "anulada"}

_METODOS_PAGO = {"efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado", "datafono"}


# ---------------------------------------------------------------- reglas globales (G2-G5)

def dinero(v) -> Decimal | None:
    """G2: dinero int (pesos) → NUMERIC, mismo valor, sin dividir."""
    return None if v is None else Decimal(v)


def combinar_fecha_hora(fecha: date, hora: time | None) -> datetime:
    """G5+G4: `fecha`+`hora` escritas por la app en hora local Colombia → aware UTC.

    Sin hora se usa mediodía Colombia: medianoche cruzaría de día al convertir a UTC y
    corrompería reportes diarios.
    """
    h = hora if hora is not None else time(12, 0)
    return datetime.combine(fecha, h, tzinfo=CO).astimezone(timezone.utc)


def utc_naive_a_aware(ts: datetime | None) -> datetime | None:
    """G4 (servidor): `created_at` naive escritos por Postgres en Etc/UTC → aware UTC, sin desplazar.

    Si ya viene aware (columnas timestamptz del legacy) se devuelve tal cual.
    """
    if ts is None:
        return None
    if ts.tzinfo is not None:
        return ts
    return ts.replace(tzinfo=timezone.utc)


def split_numero_fe(numero: str) -> tuple[str | None, int | None]:
    """T-FE: separa `numero` ('FPR100', 'ERR-53', 'FE-1234') en (prefijo, consecutivo)."""
    m = _RE_NUMERO_FE.match(numero.strip())
    if not m:
        return None, None
    return m.group(1).upper(), int(m.group(2))


def fraccion_a_decimal(fraccion: str) -> Decimal | None:
    """'1/2' → 0.5 (la columna `decimal` de productos_fracciones del destino)."""
    m = _RE_FRACCION.match(fraccion.strip())
    if not m or int(m.group(2)) == 0:
        return None
    return Decimal(m.group(1)) / Decimal(m.group(2))


# ---------------------------------------------------------------- por tabla (T-*)

def t_usuario(row: dict) -> dict:
    return {
        "id": row["id"], "telegram_id": row["telegram_id"], "nombre": row["nombre"],
        "rol": row["rol"] if row["rol"] in ("admin", "vendedor") else "vendedor",
        "activo": bool(row["activo"]),
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def t_producto(row: dict, *, fracciones_ids: set[int], codigos_vistos: set[str],
               precio_compra: Decimal | None = None) -> dict:
    """T-PRECIO: precio_unidad→precio_venta; iva compuesto; umbrales directos; codigo con
    fallback a `clave` y dedupe con sufijo estable (UNIQUE en destino)."""
    codigo = (row.get("codigo") or "").strip() or (row.get("clave") or "").strip() or None
    if codigo is not None:
        if codigo in codigos_vistos:
            codigo = f"{codigo}-{row['id']}"
        codigos_vistos.add(codigo)
    return {
        "id": row["id"], "codigo": codigo, "nombre": row["nombre"],
        "categoria": row.get("categoria"),
        "unidad_medida": row.get("unidad_medida") or "Unidad",
        "precio_venta": dinero(row.get("precio_unidad")) or Decimal(0),
        "precio_compra": precio_compra,
        "precio_umbral": dinero(row.get("precio_umbral")),
        "precio_bajo_umbral": dinero(row.get("precio_bajo_umbral")),
        "precio_sobre_umbral": dinero(row.get("precio_sobre_umbral")),
        "iva": int(row.get("porcentaje_iva") or 0) if row.get("tiene_iva") else 0,
        "permite_fraccion": row["id"] in fracciones_ids,
        "activo": bool(row.get("activo", True)),
        "creado_en": utc_naive_a_aware(row.get("created_at")),
        "actualizado_en": utc_naive_a_aware(row.get("updated_at")),
    }


def t_cliente(row: dict) -> dict:
    return {
        "id": row["id"], "nombre": row["nombre"],
        "tipo_documento": row.get("tipo_id"), "documento": row.get("identificacion"),
        "telefono": row.get("telefono"), "correo": row.get("correo"),
        "direccion": row.get("direccion"),
        # ID de municipio DIAN/MATIAS del legacy; el servicio de facturación lo resuelve en runtime.
        "ciudad_dane": str(row["municipio_dian"]) if row.get("municipio_dian") is not None else None,
        "regimen": str(row["regimen_fiscal"]) if row.get("regimen_fiscal") is not None else None,
        "saldo_fiado": Decimal(0),   # se recalcula desde fiados_movimientos (hoy vacíos)
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def t_fraccion(row: dict) -> dict:
    return {
        "id": row["id"], "producto_id": row["producto_id"], "fraccion": row["fraccion"],
        "decimal": fraccion_a_decimal(row["fraccion"]),
        "precio_total": dinero(row["precio_total"]),
        "precio_unitario": dinero(row.get("precio_unitario")),
    }


def t_ventas(rows: list[dict], *, usuarios: list[dict]) -> list[dict]:
    """T-VENTAS con renumeración global del consecutivo (orden fecha, hora, id).

    vendedor_id: usuario_id si viene; si no, match por texto `vendedor` contra usuarios.nombre;
    último recurso: el primer admin.
    """
    por_nombre = {u["nombre"].strip().lower(): u["id"] for u in usuarios if u.get("nombre")}
    admin_id = min((u["id"] for u in usuarios if u.get("rol") == "admin"),
                   default=min((u["id"] for u in usuarios), default=1))

    def _vendedor(row: dict) -> int:
        if row.get("usuario_id") is not None:
            return row["usuario_id"]
        nombre = (row.get("vendedor") or "").strip().lower()
        return por_nombre.get(nombre, admin_id)

    # La hora efectiva de ordenación debe ser LA MISMA que usa combinar_fecha_hora (mediodía
    # para las ventas sin hora); si no, el consecutivo queda no monótono con el timestamp final.
    ordenadas = sorted(rows, key=lambda r: (r["fecha"], r.get("hora") or time(12, 0), r["id"]))
    salida = []
    for consecutivo, row in enumerate(ordenadas, start=1):
        metodo = (row.get("metodo_pago") or "").strip().lower()
        total = dinero(row.get("total")) or Decimal(0)
        salida.append({
            "id": row["id"],
            "consecutivo": consecutivo,
            "cliente_id": row.get("cliente_id"),
            "vendedor_id": _vendedor(row),
            "fecha": combinar_fecha_hora(row["fecha"], row.get("hora")),
            "subtotal": total, "impuestos": Decimal(0), "total": total,   # D6
            "metodo_pago": metodo if metodo in _METODOS_PAGO else "efectivo",
            "estado": "completada",
            "origen": "web",
            "idempotency_key": None,
        })
    return salida


def t_venta_detalle(row: dict, *, iva_por_producto: dict[int, int]) -> dict:
    return {
        "id": row["id"], "venta_id": row["venta_id"], "producto_id": row.get("producto_id"),
        "descripcion": row.get("producto_nombre"),
        "cantidad": row["cantidad"],
        "precio_unitario": dinero(row.get("precio_unitario")) or Decimal(0),
        "iva": iva_por_producto.get(row.get("producto_id"), 0),
    }


def t_factura_electronica(row: dict) -> dict:
    prefijo, consecutivo = split_numero_fe(row["numero"])
    respuesta = {"numero_original": row["numero"], "cliente_nombre": row.get("cliente_nombre"),
                 "total": row.get("total")}
    if row.get("error_msg"):
        respuesta["error_msg"] = row["error_msg"]
    if row.get("razon_id") is not None:
        respuesta["razon_id"] = row["razon_id"]
    if row.get("factura_cufe_ref"):
        respuesta["factura_cufe_ref"] = row["factura_cufe_ref"]
    return {
        "id": row["id"], "venta_id": row.get("venta_id"),
        "tipo": row.get("tipo") or "factura",
        "prefijo": prefijo, "consecutivo": consecutivo,
        "cufe": row.get("cufe"),
        "estado": _FE_ESTADO.get((row.get("estado") or "").lower(), "error"),
        "dian_respuesta": respuesta,
        "intentos": 0, "idempotency_key": None,
        "emitido_en": utc_naive_a_aware(row.get("fecha_emision")),
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def t_cuenta_cobro(row: dict) -> dict:
    # El destino no tiene `fecha` ni `pdf_bytes` (el PDF se exporta a archivo en __main__).
    return {
        "id": row["id"], "consecutivo": row["consecutivo"], "numero_display": row["numero_display"],
        "periodo": row["periodo"], "concepto": row["concepto"], "valor": row["valor"],
        "cliente_id": None,   # D7: la cuenta de cobro es del operador
        "enviado_telegram": bool(row.get("enviado_telegram")),
        "creado_en": utc_naive_a_aware(row.get("creado_at")),
    }


def t_documento_soporte(row: dict) -> dict:
    return {
        "id": row["id"], "consecutivo": row.get("consecutivo"), "fecha": row.get("fecha"),
        "valor": row.get("valor"), "cude": row.get("cude"), "estado_dian": row.get("estado_dian"),
        "cuenta_cobro_id": row.get("cuenta_cobro_id"), "intentos": 0, "idempotency_key": None,
        "creado_en": utc_naive_a_aware(row.get("created_at")),
        "emitido_en": None,
    }


def t_gasto(row: dict) -> dict:
    categoria = (row.get("categoria") or "otros").strip().lower()
    return {
        "id": row["id"],
        "categoria": categoria if categoria in _GASTO_CATEGORIAS else "otros",
        "monto": dinero(row["monto"]) or Decimal(0),
        "concepto": row.get("concepto"),
        "caja_id": None,   # el legacy no vincula gasto↔caja; se reconstruye en operación nueva
        "usuario_id": row.get("usuario_id"),
        "factura_proveedor_id": row.get("fac_id") or None,
        "creado_en": combinar_fecha_hora(row["fecha"], row.get("hora")) if row.get("fecha")
        else utc_naive_a_aware(row.get("created_at")),
        "idempotency_key": None,
    }


def t_caja_abierta(row: dict) -> dict:
    """T-CAJA: solo la caja abierta migra, como apertura; el histórico diario vive en historico_ventas."""
    return {
        "id": row["id"], "usuario_id": None,
        "fecha_apertura": utc_naive_a_aware(row.get("created_at"))
        or combinar_fecha_hora(row["fecha"], None),
        "saldo_inicial": dinero(row.get("monto_apertura")) or Decimal(0),
        "estado": "abierta",
    }


def t_historico(row: dict) -> dict:
    return {
        "fecha": row["fecha"],
        "ventas": dinero(row.get("ventas")) or Decimal(0),
        "efectivo": dinero(row.get("efectivo")) or Decimal(0),
        "transferencia": dinero(row.get("transferencia")) or Decimal(0),
        "datafono": dinero(row.get("datafono")) or Decimal(0),
        "n_transacciones": row.get("n_transacciones") or 0,
        "gastos": dinero(row.get("gastos")) or Decimal(0),
        "abonos_proveedores": dinero(row.get("abonos_proveedores")) or Decimal(0),
        "origen": row.get("origen") or "calculado",
        "incluir_en_balances": bool(row.get("incluir_en_balances", True)),
        "notas": row.get("notas"),
        "actualizado_en": utc_naive_a_aware(row.get("updated_at")),
    }


def t_factura_proveedor(row: dict) -> dict:
    total = dinero(row["total"]) or Decimal(0)
    pagado = dinero(row.get("pagado")) or Decimal(0)
    return {
        "id": row["id"], "proveedor": row["proveedor"], "descripcion": row.get("descripcion"),
        "total": total, "pagado": pagado, "pendiente": total - pagado,   # recalculado (spec §4 paso 3)
        "estado": row.get("estado") or "pendiente", "fecha": row["fecha"],
        "foto_url": row.get("foto_url") or None, "foto_nombre": row.get("foto_nombre") or None,
        "usuario_id": row.get("usuario_id"),
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def t_abono(row: dict) -> dict:
    return {
        "id": row["id"], "factura_id": row.get("factura_id"),
        "monto": dinero(row["monto"]) or Decimal(0), "fecha": row["fecha"],
        "foto_url": row.get("foto_url") or None, "foto_nombre": row.get("foto_nombre") or None,
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def t_bancolombia(row: dict) -> dict:
    return {
        "id": row["id"], "gmail_message_id": row["gmail_message_id"],
        "fecha": row["fecha"], "hora": row.get("hora") or "",
        "monto": dinero(row.get("monto")) or Decimal(0),
        "remitente": row.get("remitente") or "", "descripcion": row.get("descripcion") or "",
        "tipo_transaccion": row.get("tipo_transaccion") or "", "referencia": row.get("referencia") or "",
        "notificado": bool(row.get("notificado", True)),
        "naturaleza": "credito", "estado_conciliacion": "no_conciliado",
        "creado_en": utc_naive_a_aware(row.get("created_at")),
    }


def dedupe_memoria(rows: list[dict]) -> list[dict]:
    """El destino tiene UNIQUE(tipo, clave): ante duplicados del legacy gana el más reciente."""
    por_clave: dict[tuple, dict] = {}
    for row in sorted(rows, key=lambda r: (r.get("creado_en") or datetime.min.replace(tzinfo=timezone.utc))):
        por_clave[(row.get("tipo"), row["entidad_key"])] = row
    return list(por_clave.values())


def t_memoria(row: dict) -> dict:
    return {
        "id": row["id"], "tipo": row.get("tipo"), "clave": row["entidad_key"],
        "valor": {"nota": row.get("nota"), "confidence": row.get("confidence"),
                  "fecha_generada": row["fecha_generada"].isoformat() if row.get("fecha_generada") else None,
                  "vigente": row.get("vigente", True)},
        "actualizado_en": utc_naive_a_aware(row.get("creado_en")),
    }


def t_iva_saldo(row: dict, idx: int) -> dict:
    return {
        "id": idx, "anio": row.get("año") or row.get("anio"), "bimestre": row["bimestre"],
        "iva_generado": dinero(row.get("iva_ventas")) or Decimal(0),
        "iva_descontable": dinero(row.get("iva_compras")) or Decimal(0),
        "saldo": dinero(row.get("iva_neto")) or Decimal(0),
    }


def t_fiado(row: dict) -> dict | None:
    if row.get("cliente_id") is None:   # destino exige cliente; sin cliente no hay fiado migrable
        return None
    return {
        "id": row["id"], "cliente_id": row["cliente_id"], "venta_id": None,
        "monto": dinero(row.get("saldo_actual")) or Decimal(0),
        "saldo": dinero(row.get("saldo_actual")) or Decimal(0),
        "creado_en": utc_naive_a_aware(row.get("created_at")),
        "idempotency_key": None,
    }


def t_fiado_movimiento(row: dict) -> dict:
    cargo = row.get("cargo") or 0
    tipo, monto = ("cargo", cargo) if cargo else ("abono", row.get("abono") or 0)
    return {
        "id": row["id"], "fiado_id": row["fiado_id"], "tipo": tipo,
        "monto": Decimal(monto),
        "creado_en": combinar_fecha_hora(row["fecha"], row.get("hora")) if row.get("fecha")
        else utc_naive_a_aware(row.get("creado_at")),
        "idempotency_key": None,
    }


# ---------------------------------------------------------------- derivadas

def derivar_aliases(aliases_rows: list[dict], productos_rows: list[dict]) -> list[dict]:
    """Tabla `aliases` legacy + arrays `productos.aliases[]` → tabla aliases del destino.

    IDs deterministas 1..n (el legacy no los tiene y el destino los necesita para idempotencia).
    Dedupe por término (UNIQUE en destino); primero la tabla explícita, luego los arrays.
    """
    vistos: set[str] = set()
    salida: list[dict] = []
    for row in aliases_rows:
        termino = row["termino"].strip().lower()
        if termino and termino not in vistos:
            vistos.add(termino)
            salida.append({"termino": termino, "reemplazo": row["reemplazo"], "producto_id": None})
    for p in productos_rows:
        for alias in (p.get("aliases") or []):
            termino = (alias or "").strip().lower()
            if termino and termino not in vistos:
                vistos.add(termino)
                salida.append({"termino": termino, "reemplazo": p["nombre"], "producto_id": p["id"]})
    for i, fila in enumerate(salida, start=1):
        fila["id"] = i
    return salida


def derivar_proveedores(origen: dict) -> tuple[list[dict], dict[str, int]]:
    """D10: DISTINCT de los textos libres de proveedor → tabla proveedores + mapa texto→id."""
    nombres: set[str] = set()
    for tabla in ("compras", "compras_fiscal", "facturas_proveedores"):
        for row in origen.get(tabla, []):
            nombre = (row.get("proveedor") or "").strip()
            if nombre:
                nombres.add(nombre)
    filas, mapa = [], {}
    for i, nombre in enumerate(sorted(nombres), start=1):
        filas.append({"id": i, "nombre": nombre, "nit": None, "telefono": None, "correo": None})
        mapa[nombre] = i
    return filas, mapa


def derivar_movimientos_inventario(inventario_rows: list[dict]) -> list[dict]:
    """Invariante #7: el saldo inicial migrado entra por kardex — 1 AJUSTE por producto con stock>0."""
    salida = []
    for i, row in enumerate((r for r in inventario_rows if (r.get("cantidad") or 0) > 0), start=1):
        salida.append({
            "id": i, "producto_id": row["producto_id"], "tipo": "AJUSTE",
            "cantidad": row["cantidad"], "costo_unitario": row.get("ultimo_costo"),
            "referencia": "migracion", "usuario_id": None,
            "idempotency_key": f"migracion:{row['producto_id']}",
            "fecha_operacion": utc_naive_a_aware(row.get("updated_at")),
        })
    return salida


def derivar_compras_fiscal(rows: list[dict], mapa_proveedores: dict[str, int],
                           base_compras_id: int) -> tuple[list[dict], list[dict], list[dict]]:
    """compras_fiscal legacy (fila por factura de compra con producto embebido) →
    compras + compras_detalle + compras_fiscal del destino.

    El destino no tiene proveedor en compras_fiscal (solo NIT, que el legacy no guarda):
    el vínculo con el proveedor queda en la compra derivada.
    """
    compras, detalles, fiscales = [], [], []
    for i, row in enumerate(rows, start=1):
        compra_id = base_compras_id + i
        total = dinero(row.get("costo_total")) or Decimal(0)
        if row.get("incluye_iva") and (row.get("tarifa_iva") or 0) > 0:
            base = (total / (1 + Decimal(row["tarifa_iva"]) / 100)).quantize(Decimal("0.01"))
        else:
            base = total
        compras.append({
            "id": compra_id,
            "proveedor_id": mapa_proveedores.get((row.get("proveedor") or "").strip()),
            "fecha": combinar_fecha_hora(row["fecha"], row.get("hora")),
            "total": total, "idempotency_key": None,
            "creado_en": utc_naive_a_aware(row.get("created_at")),
        })
        if row.get("producto_id") is not None:
            detalles.append({"id": compra_id, "compra_id": compra_id,
                             "producto_id": row["producto_id"], "cantidad": row["cantidad"],
                             "costo": dinero(row.get("costo_unitario"))})
        fiscales.append({
            "id": row["id"], "compra_id": compra_id, "proveedor_nit": None,
            "base": base, "iva": total - base, "total": total, "soporte_url": None,
            "cufe_proveedor": row.get("cufe_proveedor"),
            "evento_030_at": utc_naive_a_aware(row.get("evento_030_at")),
            "evento_031_at": utc_naive_a_aware(row.get("evento_031_at")),
            "evento_032_at": utc_naive_a_aware(row.get("evento_032_at")),
            "evento_033_at": utc_naive_a_aware(row.get("evento_033_at")),
            "evento_estado": row.get("evento_estado") or "pendiente",
            "evento_error": row.get("evento_error"),
            "creado_en": utc_naive_a_aware(row.get("created_at")),
        })
    return compras, detalles, fiscales


# ---------------------------------------------------------------- orquestador

def transformar(origen: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Origen legacy completo → {tabla destino: filas destino} en el orden de carga (§5)."""
    fracciones_ids = {r["producto_id"] for r in origen.get("productos_fracciones", [])
                      if r.get("producto_id") is not None}
    costo_por_producto = {r["producto_id"]: r.get("ultimo_costo")
                          for r in origen.get("inventario", [])}
    codigos_vistos: set[str] = set()
    productos = [
        t_producto(r, fracciones_ids=fracciones_ids, codigos_vistos=codigos_vistos,
                   precio_compra=costo_por_producto.get(r["id"]))
        for r in origen.get("productos", [])
    ]
    iva_por_producto = {p["id"]: p["iva"] for p in productos}

    proveedores, mapa_prov = derivar_proveedores(origen)
    max_compra_id = max((r["id"] for r in origen.get("compras", [])), default=0)
    compras_derivadas, detalles_derivados, compras_fiscal = derivar_compras_fiscal(
        origen.get("compras_fiscal", []), mapa_prov, base_compras_id=max_compra_id)

    datos: dict[str, list[dict]] = {
        "usuarios": [t_usuario(r) for r in origen.get("usuarios", [])],
        "clientes": [t_cliente(r) for r in origen.get("clientes", [])],
        "productos": productos,
        "productos_fracciones": [t_fraccion(r) for r in origen.get("productos_fracciones", [])],
        "aliases": derivar_aliases(origen.get("aliases", []), origen.get("productos", [])),
        "inventario": [
            {"producto_id": r["producto_id"], "stock_actual": r.get("cantidad") or Decimal(0),
             "stock_minimo": r.get("minimo") or Decimal(0),
             "actualizado_en": utc_naive_a_aware(r.get("updated_at"))}
            for r in origen.get("inventario", [])
        ],
        "movimientos_inventario": derivar_movimientos_inventario(origen.get("inventario", [])),
        "proveedores": proveedores,
        "facturas_proveedores": [t_factura_proveedor(r) for r in origen.get("facturas_proveedores", [])],
        "facturas_abonos": [t_abono(r) for r in origen.get("facturas_abonos", [])],
        "ventas": t_ventas(origen.get("ventas", []), usuarios=origen.get("usuarios", [])),
        "ventas_detalle": [t_venta_detalle(r, iva_por_producto=iva_por_producto)
                           for r in origen.get("ventas_detalle", [])],
        "historico_ventas": [t_historico(r) for r in origen.get("historico_ventas", [])],
        "facturas_electronicas": [t_factura_electronica(r) for r in origen.get("facturas_electronicas", [])],
        "cuentas_cobro": [t_cuenta_cobro(r) for r in origen.get("cuentas_cobro", [])],
        "documentos_soporte": [t_documento_soporte(r) for r in origen.get("documentos_soporte", [])],
        "compras": compras_derivadas,
        "compras_detalle": detalles_derivados,
        "compras_fiscal": compras_fiscal,
        "gastos": [t_gasto(r) for r in origen.get("gastos", [])],
        "caja": [t_caja_abierta(r) for r in origen.get("caja", []) if r.get("abierta")],
        "fiados": [f for f in (t_fiado(r) for r in origen.get("fiados", [])) if f],
        "fiados_movimientos": [t_fiado_movimiento(r) for r in origen.get("fiados_movimientos", [])],
        "bancolombia_transferencias": [t_bancolombia(r) for r in origen.get("bancolombia_transferencias", [])],
        "iva_saldos_bimestrales": [t_iva_saldo(r, i) for i, r in
                                   enumerate(origen.get("iva_saldos_bimestrales", []), start=1)],
        "memoria_entidades": [t_memoria(r) for r in dedupe_memoria(origen.get("memoria_entidades", []))],
    }
    return datos
