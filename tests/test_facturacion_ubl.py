"""Fase 6 / E1 — núcleo UBL puro de la factura electrónica (sin SQL/red).

Pin del contrato de `docs/facturacion-matias-extract.md` §3/§4/§8/§9: mapas de IDs, math de línea
con IVA incluido, tax_totals/legal_monetary_totals, 3 casos de cliente y pre-check FAU04.

NOTA: la carpeta original `bot-ventas-ferreteria/services/facturacion_service.py` NO está en este
repo, así que los valores esperados de los mapas se anclan al DOC (§3/§4). El cruce VERBATIM contra
el original (clave por clave, incluidos los alias de tipo/unidad) queda como checkpoint de GREEN.
"""
from datetime import date, time
from decimal import Decimal

import pytest

from modules.facturacion import ubl
from modules.facturacion.schemas import ClienteFiscal, DatosEmision, FacturaInput, ItemFactura

# --- valores esperados del doc (§3/§4) ---------------------------------------
EXP_TIPO_ID_MATIAS = {
    "CC": "1", "CE": "2", "NIT": "3", "RC": "6", "TI": "7", "TE": "8", "PA": "9", "PPN": "9",
    "DE": "10", "NITE": "11", "NUIP": "12", "PPT": "13", "PP": "13", "PEP": "14", "PE": "14",
    "SC": "15", "CN": "16", "AS": "17", "MS": "18", "SI": "19", "CD": "20",
}
EXP_TIPO_ID_DIAN = {
    "CC": "13", "CE": "22", "NIT": "31", "RC": "11", "TI": "12", "TE": "21", "PA": "41", "PPN": "41",
    "DE": "42", "NITE": "50", "NUIP": "91", "PPT": "48", "PEP": "47", "SC": "SC", "CD": "CD",
}
EXP_UNIDAD_DIAN = {
    "Unidad": 70, "unidad": 70, "Galón": 686, "galon": 686, "Gal": 686, "Kg": 767, "kg": 767,
    "GRM": 692, "gramo": 692, "Mts": 865, "Mt": 865, "metro": 865, "Cms": 495, "Cm": 495,
    "Lt": 821, "Lts": 821, "litro": 821, "MLT": 852, "ml": 852,
}
EXP_MEDIOS_PAGO = {
    "efectivo": 10, "transferencia": 42, "nequi": 42, "daviplata": 42,
    "tarjeta": 48, "datafono": 48,
}


# --- helpers -----------------------------------------------------------------

def _item(precio="11900", cant="1", pct="19", unidad="Unidad", pid=5, desc="martillo") -> ItemFactura:
    return ItemFactura(
        producto_id=pid, descripcion=desc, cantidad=Decimal(cant),
        precio_unitario_con_iva=Decimal(precio), pct_iva=Decimal(pct), unidad=unidad,
    )


def _factura() -> FacturaInput:
    emision = DatosEmision(
        resolution_number="18760000001", prefix="FPR", document_number="1024",
        fecha=date(2026, 6, 4), hora=time(10, 30, 0),
        means_payment_id=10, payment_method_id=1, notes="Punto Rojo",
    )
    return FacturaInput(
        emision=emision, cliente=ClienteFiscal(nombre="Cliente Mostrador"),
        items=[_item(precio="11900", cant="1", pct="19", pid=5)],
    )


# --- mapas verbatim (ancla de paridad) ---------------------------------------

def test_mapas_verbatim():
    assert ubl._TIPO_ID_MATIAS == EXP_TIPO_ID_MATIAS
    assert ubl._TIPO_ID_DIAN == EXP_TIPO_ID_DIAN
    assert ubl._UNIDAD_DIAN == EXP_UNIDAD_DIAN
    assert ubl._MEDIOS_PAGO == EXP_MEDIOS_PAGO
    assert (ubl.CURRENCY_COP, ubl.TYPE_DOC_FE, ubl.OPERATION_FE) == (272, 7, 1)


# --- math de línea (§8.2) ----------------------------------------------------

def test_linea_gravada_iva19():
    lineas, _acc = ubl.armar_lineas([_item(precio="11900", cant="1", pct="19", pid=5)])
    linea = lineas[0]
    assert linea["line_extension_amount"] == Decimal("10000.00")
    assert linea["line_extension_amount"].as_tuple().exponent == -2     # 2 decimales
    assert linea["price_amount"] == Decimal("10000.00")
    assert linea["code"] == "5"
    assert linea["type_item_identifications_id"] == "4"
    tt = linea["tax_totals"][0]
    assert tt["tax_id"] == "1"
    assert tt["taxable_amount"] == Decimal("10000.00")
    assert tt["tax_amount"] == Decimal("1900.00")
    assert tt["percent"] == Decimal("19")


def test_linea_exenta():
    lineas, _acc = ubl.armar_lineas([_item(precio="10000", cant="1", pct="0", pid=7)])
    linea = lineas[0]
    tt = linea["tax_totals"][0]
    assert tt["tax_id"] == "1"                       # NUNCA "4" (eso es INC → FAX14)
    assert tt["percent"] == Decimal("0")
    assert tt["tax_amount"] == Decimal("0.00")
    assert tt["taxable_amount"] == linea["line_extension_amount"] == Decimal("10000.00")


def test_linea_fraccion():
    lineas, _acc = ubl.armar_lineas([_item(precio="16000", cant="0.0625", pct="19")])
    linea = lineas[0]
    assert linea["invoiced_quantity"] == Decimal("0.0625")
    assert linea["invoiced_quantity"].as_tuple().exponent == -4         # 4 decimales
    assert linea["base_quantity"] == linea["invoiced_quantity"]


# --- totales del documento (§8.3 / §8.4) -------------------------------------

def test_tax_totals_mixto():
    items = [_item(precio="11900", cant="1", pct="19", pid=1),
             _item(precio="10000", cant="1", pct="0", pid=2)]
    _lineas, acc = ubl.armar_lineas(items)
    tts = ubl.armar_tax_totals(acc)
    assert len(tts) == 2
    assert {t["percent"] for t in tts} == {Decimal("19"), Decimal("0")}
    assert all(t["tax_id"] == "1" for t in tts)
    grav = next(t for t in tts if t["percent"] == Decimal("19"))
    exen = next(t for t in tts if t["percent"] == Decimal("0"))
    assert (grav["taxable_amount"], grav["tax_amount"]) == (Decimal("10000.00"), Decimal("1900.00"))
    assert (exen["taxable_amount"], exen["tax_amount"]) == (Decimal("10000.00"), Decimal("0.00"))


def test_legal_monetary_totals():
    _lineas, acc = ubl.armar_lineas([_item(precio="11900", cant="1", pct="19", pid=1)])
    lmt = ubl.armar_legal_monetary_totals(acc)
    assert lmt["line_extension_amount"] == Decimal("10000.00")
    assert lmt["tax_exclusive_amount"] == Decimal("10000.00")
    assert lmt["tax_inclusive_amount"] == Decimal("11900.00")
    assert lmt["payable_amount"] == Decimal("11900.00")
    for clave in ("allowance_total_amount", "charge_total_amount", "pre_paid_amount"):
        assert lmt[clave] == Decimal("0.00")


# --- cliente (§8.1, 3 casos) -------------------------------------------------

def test_customer_consumidor_final():
    cust = ubl.armar_customer(ClienteFiscal(nombre="Cliente Mostrador"))   # sin numero → CF
    assert cust["identity_document_id"] == "6"
    assert cust["dni"] == "222222222222"
    assert cust["company_name"] == "CONSUMIDOR FINAL"
    assert cust["tax_level_id"] == 5
    assert cust["tax_regime_id"] == 2
    assert "name" not in cust
    assert cust["country_id"] == "45"          # string, no int


def test_customer_nit_con_dv():
    resp = ubl.armar_customer(
        ClienteFiscal(tipo_documento="NIT", numero="900123456-5", regimen_fiscal=1, nombre="Ferre SAS")
    )
    assert resp["dni"] == "900123456"
    assert resp["dv"] == "5"
    assert resp["identity_document_id"] == "3"
    assert resp["type_organization_id"] == 1
    assert resp["tax_level_id"] == 1                                       # responsable IVA
    assert resp["tax_regime_id"] == 1                                      # responsable
    no_resp = ubl.armar_customer(
        ClienteFiscal(tipo_documento="NIT", numero="900123456-5", regimen_fiscal=2, nombre="Ferre SAS")
    )
    assert no_resp["tax_level_id"] == 5                                    # no responsable
    assert no_resp["tax_regime_id"] == 2                                   # no responsable


def test_customer_persona_ce():
    cust = ubl.armar_customer(ClienteFiscal(tipo_documento="CE", numero="123456", nombre="Juan"))
    assert cust["identity_document_id"] == ubl._TIPO_ID_MATIAS["CE"]       # NUNCA hardcodear "1"
    assert cust["tax_regime_id"] == 2
    assert cust["tax_level_id"] == 5


def test_customer_city_desde_input():
    cust = ubl.armar_customer(ClienteFiscal(tipo_documento="CC", numero="123", nombre="X",
            city_id_matias="149", city_name="CARTAGENA"))
    assert cust["city_id"] == "149" and cust["city_name"] == "CARTAGENA"
    # y que NO hay default de una empresa: con city vacío, city_id queda "" (lo llena E3), no "1006".
    cf = ubl.armar_customer(ClienteFiscal(nombre="Y"))
    assert cf["city_id"] == "" and cf["city_name"] == ""


def test_payload_campos_dian():
    p = ubl.armar_payload_factura(_factura())          # _factura usa CF sin correo
    assert p["graphic_representation"] == 1
    assert p["send_email"] == 0                          # correo placeholder → 0
    # con correo real:
    f2 = FacturaInput(emision=_factura().emision,
         cliente=ClienteFiscal(tipo_documento="CC", numero="123", nombre="X",
                               email="real@cliente.com"),
         items=[_item()])
    assert ubl.armar_payload_factura(f2)["send_email"] == 1


# --- pre-check FAU04 (§9) ----------------------------------------------------

def test_validar_bases_ok():
    lineas = [
        {"tax_totals": [{"taxable_amount": Decimal("10000.00"), "percent": Decimal("19")}]},
        {"tax_totals": [{"taxable_amount": Decimal("5000.00"), "percent": Decimal("19")}]},
    ]
    tax_totals_doc = [{"taxable_amount": Decimal("15000.00"), "percent": Decimal("19")}]
    assert ubl.validar_bases(lineas, tax_totals_doc) is None               # no lanza


def test_validar_bases_fau04_falla():
    lineas = [{"tax_totals": [{"taxable_amount": Decimal("10000.00"), "percent": Decimal("19")}]}]
    tax_totals_doc = [{"taxable_amount": Decimal("10000.50"), "percent": Decimal("19")}]  # dif 0.50
    with pytest.raises(ubl.BaseDescuadradaError):
        ubl.validar_bases(lineas, tax_totals_doc)


# --- ensamblaje del payload (§8) ---------------------------------------------

def test_armar_payload_factura_estructura():
    payload = ubl.armar_payload_factura(_factura())
    assert payload["type_document_id"] == 7
    assert payload["operation_type_id"] == 1
    assert payload["currency_id"] == 272
    assert payload["prefix"] == "FPR"
    assert payload["resolution_number"] == "18760000001"
    assert payload["document_number"] == "1024"
    pago = payload["payments"][0]
    assert pago["means_payment_id"] == 10
    assert pago["payment_method_id"] == 1
    assert payload["lines"]                                                # no vacío
