"""Núcleo UBL puro de la factura electrónica (sin SQL/red).

Mapas de IDs + math de línea con IVA incluido + pre-checks DIAN. Contrato exacto en
`docs/facturacion-matias-extract.md` §3, §4, §8, §9. Decimal puro (nunca float); 2 decimales con
`core.money.cuantizar`.

Los mapas se portan VERBATIM de `bot-ventas-ferreteria/services/facturacion_service.py`: `_TIPO_ID_MATIAS`
sí tiene los alias PP/PE/CN/AS/MS/SI; `_TIPO_ID_DIAN` NO los tiene (solo SC/CD literales).
"""
from __future__ import annotations

from decimal import Decimal

from core.money import cuantizar, descomponer_iva
from modules.facturacion.schemas import ClienteFiscal, FacturaInput, ItemFactura, PosInput

# --- Mapas de IDs (verbatim §3/§4) -------------------------------------------
# §3 — tipo de documento en POST de creación (identity_document_id). .get(tipo, "1") por defecto.
_TIPO_ID_MATIAS: dict[str, str] = {
    "CC": "1", "CE": "2", "NIT": "3", "RC": "6", "TI": "7", "TE": "8", "PA": "9", "PPN": "9",
    "DE": "10", "NITE": "11", "NUIP": "12", "PPT": "13", "PP": "13", "PEP": "14", "PE": "14",
    "SC": "15", "CN": "16", "AS": "17", "MS": "18", "SI": "19", "CD": "20",
}
# §3 — tipo de documento en GET /acquirer (códigos DIAN); SC/CD quedan literales.
_TIPO_ID_DIAN: dict[str, str] = {
    "CC": "13", "CE": "22", "NIT": "31", "RC": "11", "TI": "12", "TE": "21", "PA": "41", "PPN": "41",
    "DE": "42", "NITE": "50", "NUIP": "91", "PPT": "48", "PEP": "47", "SC": "SC", "CD": "CD",
}
# §4 — unidades de medida → quantity_units_id (default 70 = Unidad).
_UNIDAD_DIAN: dict[str, int] = {
    "Unidad": 70, "unidad": 70, "Galón": 686, "galon": 686, "Gal": 686, "Kg": 767, "kg": 767,
    "GRM": 692, "gramo": 692, "Mts": 865, "Mt": 865, "metro": 865, "Cms": 495, "Cm": 495,
    "Lt": 821, "Lts": 821, "litro": 821, "MLT": 852, "ml": 852,
}
# §4 — medios de pago → means_payment_id.
_MEDIOS_PAGO: dict[str, int] = {
    "efectivo": 10, "transferencia": 42, "tarjeta": 48, "nequi": 42, "daviplata": 42, "datafono": 48,
}

# --- IDs fijos §4 ------------------------------------------------------------
CURRENCY_COP = 272      # COP
TYPE_DOC_FE = 7         # factura electrónica
OPERATION_FE = 1        # operación FE (el tipo de cliente define CF/normal, no esto)
TYPE_DOC_POS = 20       # documento equivalente POS electrónico (id INTERNO MATIAS, NUNCA el code DIAN; ADR 0012 D4)

# Documento de consumidor final (§8.1).
_DOC_CONSUMIDOR_FINAL = "222222222222"
# Unidad por defecto si la unidad no mapea (§4).
_UNIDAD_DEFAULT = 70
# Tolerancia FAU04 (§9): descuadre máximo permitido entre líneas y cabecera.
_TOLERANCIA_FAU04 = Decimal("0.01")
# Cuantización de cantidades (4 decimales).
_CUARTO = Decimal("0.0001")

# Fallbacks de contacto genéricos (§8.1, TENANT-NEUTRAL): MATIAS rechaza campos vacíos. NO van
# valores de empresa (ciudad/dominio de correo): la ciudad llega por el input; E3 podrá sobreescribir
# el correo de respaldo por empresa más adelante.
_EMAIL_PLACEHOLDER = "sinfactura@sincorreo.co"
_MOBILE_DEFAULT = "3000000000"
_ADDRESS_DEFAULT = "NO REGISTRA"
_COMPANY_DEFAULT = "SIN NOMBRE"

# Normalización de régimen fiscal (espejo del original): → 1 (responsable) | 2 (no responsable).
_REGIMEN_NO_RESP = {"no_responsable_iva", "no_responsable", "no responsable"}
_REGIMEN_RESP = {"responsable_iva", "responsable"}


class BaseDescuadradaError(ValueError):
    """FAU04: la suma de `taxable_amount` de las líneas no cuadra con la cabecera (dif > 0.01)."""


# --- cliente (§8.1) ----------------------------------------------------------

def _campos_contacto(c: ClienteFiscal) -> dict:
    """Contacto + ubicación tenant-neutral: contacto con placeholders genéricos; ciudad SOLO del
    input (sin inventar default de empresa, lo resuelve E3); `country_id` como string."""
    return {
        "mobile": (c.mobile or "").strip() or _MOBILE_DEFAULT,
        "email": (c.email or "").strip() or _EMAIL_PLACEHOLDER,
        "address": (c.address or "").strip() or _ADDRESS_DEFAULT,
        "country_id": str(c.country_id or 45),
        "city_id": (c.city_id_matias or "").strip(),
        "city_name": (c.city_name or "").strip(),
    }


def _normalizar_regimen(regimen: int | str | None) -> int:
    """Régimen fiscal → 1 (responsable de IVA) | 2 (no responsable); default 2 (espejo del original)."""
    if isinstance(regimen, str):
        txt = regimen.strip().lower()
        if txt in _REGIMEN_NO_RESP:
            return 2
        if txt in _REGIMEN_RESP:
            return 1
    try:
        return 1 if int(regimen) == 1 else 2
    except (TypeError, ValueError):
        return 2


def _customer_consumidor_final(c: ClienteFiscal) -> dict:
    """Caso CF: documento genérico 222222222222, no responsable."""
    base = _campos_contacto(c)
    base.update({
        "identity_document_id": "6", "type_organization_id": 2, "tax_regime_id": 2,
        "tax_level_id": 5, "dni": _DOC_CONSUMIDOR_FINAL, "company_name": "CONSUMIDOR FINAL",
    })
    return base


def _customer_nit(c: ClienteFiscal) -> dict:
    """Caso NIT (persona jurídica): separa dni/dv de '900123456-5'; régimen → tax_regime/tax_level."""
    numero = (c.numero or "").strip()
    if "-" in numero:
        dni, _, dv = numero.partition("-")
    else:
        dni, dv = numero, (c.dv or "").strip()
    regimen = _normalizar_regimen(c.regimen_fiscal)
    base = _campos_contacto(c)
    base.update({
        "identity_document_id": "3", "type_organization_id": 1, "dni": dni, "dv": dv,
        "tax_regime_id": regimen, "tax_level_id": 1 if regimen == 1 else 5,
        "company_name": (c.nombre or "").strip().upper() or _COMPANY_DEFAULT,
    })
    return base


def _customer_persona(c: ClienteFiscal) -> dict:
    """Caso persona natural: identity_document_id por _TIPO_ID_MATIAS (NUNCA hardcodear '1')."""
    base = _campos_contacto(c)
    base.update({
        "identity_document_id": _TIPO_ID_MATIAS.get(c.tipo_documento, "1"),
        "type_organization_id": 2, "tax_regime_id": 2, "tax_level_id": 5,
        "dni": (c.numero or "").strip(),
        "company_name": (c.nombre or "").strip().upper() or _COMPANY_DEFAULT,
    })
    return base


def armar_customer(c: ClienteFiscal) -> dict:
    """`customer` UBL — 3 casos del §8.1 (consumidor final / NIT / persona)."""
    numero = (c.numero or "").strip()
    if not numero or numero == _DOC_CONSUMIDOR_FINAL:
        return _customer_consumidor_final(c)
    if c.tipo_documento == "NIT":
        return _customer_nit(c)
    return _customer_persona(c)


# --- líneas y totales (§8.2 / §8.3 / §8.4) -----------------------------------

def _math_linea(item: ItemFactura) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Saca base/IVA/precio unitario de un precio con IVA incluido (Decimal puro).

    Base/IVA salen de `core.money.descomponer_iva` (la MISMA descomposición que usa ventas):
    una sola verdad de redondeo, la venta y su documento no difieren ni un centavo."""
    pct = item.pct_iva
    total_con_iva = item.precio_unitario_con_iva * item.cantidad
    base, iva = descomponer_iva(total_con_iva, pct)
    price = cuantizar(item.precio_unitario_con_iva / (Decimal(1) + pct / Decimal(100)))
    return pct, base, iva, price


def armar_lineas(items: list[ItemFactura]) -> tuple[list[dict], dict]:
    """Líneas UBL (§8.2) + acumulador de bases/IVA por porcentaje (para §8.3/§8.4).

    IVA incluido en BD; se redondea por línea ANTES de acumular. `tax_id` siempre "1" (también
    exento → percent=0, tax_amount=0; taxable_amount==base). Devuelve (lineas, acc).
    """
    lineas: list[dict] = []
    acc: dict[Decimal, dict] = {}
    for it in items:
        pct, base, iva, price = _math_linea(it)
        cant = it.cantidad.quantize(_CUARTO)
        lineas.append({
            "code": str(it.producto_id) if it.producto_id else "SC",
            "description": it.descripcion.upper(),
            "type_item_identifications_id": "4",
            "reference_price_id": "1",
            "invoiced_quantity": cant,
            "base_quantity": cant,
            "quantity_units_id": _UNIDAD_DIAN.get(it.unidad, _UNIDAD_DEFAULT),
            "line_extension_amount": base,
            "price_amount": price,
            "tax_totals": [{
                "tax_id": "1", "percent": pct, "taxable_amount": base, "tax_amount": iva,
            }],
        })
        bucket = acc.setdefault(pct, {"taxable_amount": Decimal("0.00"), "tax_amount": Decimal("0.00")})
        bucket["taxable_amount"] += base
        bucket["tax_amount"] += iva
    return lineas, acc


def armar_tax_totals(acc: dict) -> list[dict]:
    """`tax_totals` del documento (§8.3): una entrada por bucket de percent, `tax_id="1"`."""
    return [
        {"tax_id": "1", "percent": pct,
         "taxable_amount": vals["taxable_amount"], "tax_amount": vals["tax_amount"]}
        for pct, vals in acc.items()
    ]


def armar_legal_monetary_totals(acc: dict) -> dict:
    """`legal_monetary_totals` (§8.4): line_extension=tax_exclusive=sin IVA; tax_inclusive=payable=con IVA."""
    sin_iva = sum((v["taxable_amount"] for v in acc.values()), Decimal("0.00"))
    con_iva = sin_iva + sum((v["tax_amount"] for v in acc.values()), Decimal("0.00"))
    return {
        "line_extension_amount": sin_iva,
        "tax_exclusive_amount": sin_iva,
        "tax_inclusive_amount": con_iva,
        "payable_amount": con_iva,
        "allowance_total_amount": Decimal("0.00"),
        "charge_total_amount": Decimal("0.00"),
        "pre_paid_amount": Decimal("0.00"),
    }


# --- pre-check FAU04 (§9) ----------------------------------------------------

def validar_bases(lineas: list[dict], tax_totals_doc: list[dict]) -> None:
    """Pre-check FAU04 (§9): aborta si por bucket |sum(líneas.taxable) − cabecera.taxable| > 0.01."""
    por_linea: dict[Decimal, Decimal] = {}
    for ln in lineas:
        for tt in ln["tax_totals"]:
            por_linea[tt["percent"]] = por_linea.get(tt["percent"], Decimal("0")) + tt["taxable_amount"]
    por_doc = {tt["percent"]: tt["taxable_amount"] for tt in tax_totals_doc}
    for pct in set(por_linea) | set(por_doc):
        dif = abs(por_linea.get(pct, Decimal("0")) - por_doc.get(pct, Decimal("0")))
        if dif > _TOLERANCIA_FAU04:
            raise BaseDescuadradaError(
                f"Base descuadrada en percent={pct}: líneas vs cabecera difieren {dif}"
            )
    return None


# --- ensamblaje del payload (§8) ---------------------------------------------

def _correo_es_placeholder(email: str) -> bool:
    """True si el correo es vacío o el placeholder genérico (espejo de `_sin_correo_real`)."""
    return not email.strip() or email.strip().lower() == _EMAIL_PLACEHOLDER


def armar_payload_factura(f: FacturaInput) -> dict:
    """Ensambla el payload UBL completo (§8) y corre `validar_bases` antes de devolver."""
    e = f.emision
    customer = armar_customer(f.cliente)
    lineas, acc = armar_lineas(f.items)
    tax_totals = armar_tax_totals(acc)
    legal_monetary_totals = armar_legal_monetary_totals(acc)
    validar_bases(lineas, tax_totals)
    total_con_iva = legal_monetary_totals["payable_amount"]
    return {
        "resolution_number": e.resolution_number,
        "prefix": e.prefix,
        "document_number": e.document_number,
        "type_document_id": TYPE_DOC_FE,
        "operation_type_id": OPERATION_FE,
        "currency_id": CURRENCY_COP,
        "date": e.fecha.isoformat(),
        # MATIAS exige `time` en H:i:s estricto: `isoformat()` arrastra microsegundos del timestamp
        # de la venta ("20:35:47.123456") y el endpoint lo rechaza. `date` (Y-m-d) sí es correcto.
        "time": e.hora.strftime("%H:%M:%S"),
        "notes": e.notes,
        "graphic_representation": 1,
        "send_email": 0 if _correo_es_placeholder(customer["email"]) else 1,
        "customer": customer,
        "lines": lineas,
        "tax_totals": tax_totals,
        "legal_monetary_totals": legal_monetary_totals,
        "payments": [{
            "payment_method_id": e.payment_method_id,
            "means_payment_id": e.means_payment_id,
            "value_paid": total_con_iva,
        }],
    }


def armar_payload_pos(p: PosInput) -> dict:
    """Ensambla el payload del documento equivalente POS electrónico (ADR 0012 D4/D5). Reusa el núcleo FE.

    Diferencias clave vs factura: `type_document_id=20` (id interno MATIAS); SIN `prefix`/`document_number`
    (MATIAS los asigna por autoincremento en `/auto-increment/pos-documents`); lleva el objeto
    `point_of_sale` (cajero/terminal/dirección/tipo/código de venta/subtotal). Misma math de líneas, mismo
    pre-check FAU04 y mismos totales que la FE."""
    e = p.emision
    pv = p.punto_venta
    sw = p.software
    customer = armar_customer(p.cliente)
    lineas, acc = armar_lineas(p.items)
    # El endpoint POS exige `free_of_charge_indicator` por línea (la FE no): se añade SOLO aquí, sin
    # tocar `armar_lineas` (compartido con la FE, en producción). Mostrador nunca regala ítems → False.
    for ln in lineas:
        ln["free_of_charge_indicator"] = False
    tax_totals = armar_tax_totals(acc)
    legal_monetary_totals = armar_legal_monetary_totals(acc)
    validar_bases(lineas, tax_totals)
    total_con_iva = legal_monetary_totals["payable_amount"]
    payload = {
        "resolution_number": e.resolution_number,
        "type_document_id": TYPE_DOC_POS,
        "operation_type_id": OPERATION_FE,   # operación normal; el tipo de cliente define CF/normal
        "currency_id": CURRENCY_COP,
        "date": e.fecha.isoformat(),
        # MATIAS exige `time` en H:i:s estricto: `isoformat()` arrastra microsegundos del timestamp
        # de la venta ("20:35:47.123456") y el endpoint lo rechaza. `date` (Y-m-d) sí es correcto.
        "time": e.hora.strftime("%H:%M:%S"),
        "notes": e.notes,
        "graphic_representation": 1,
        "send_email": 0 if _correo_es_placeholder(customer["email"]) else 1,
        # `software_manufacturer` lo exige el endpoint POS por autoincremento (la FE no lo lleva).
        "software_manufacturer": {
            "owner_name": sw.owner_name,
            "company_name": sw.company_name,
            "software_name": sw.software_name,
        },
        "customer": customer,
        "lines": lineas,
        "tax_totals": tax_totals,
        "legal_monetary_totals": legal_monetary_totals,
        "payments": [{
            "payment_method_id": e.payment_method_id,
            "means_payment_id": e.means_payment_id,
            "value_paid": total_con_iva,
        }],
        "point_of_sale": {
            "cashier_name": pv.cashier_name,
            "terminal_number": pv.terminal_number,
            "address": pv.address,
            "cashier_type": pv.cashier_type,
            "sales_code": pv.sales_code,
            "sub_total": pv.sub_total,
        },
    }
    # El prefijo desambigua la resolución (varios tipos comparten resolution_number); MATIAS
    # autoincrementa solo el número. Sin prefijo el endpoint responde 404 (ADR 0012 D4, corregido).
    if e.prefix is not None:
        payload["prefix"] = e.prefix
    return payload
