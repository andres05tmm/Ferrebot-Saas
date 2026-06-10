"""Modelos de input del núcleo UBL (dominio PURO: Decimal, sin SQL/red).

Son lo que E3 (servicio + repositorios) construirá desde la venta y el cliente; E1 solo consume
estos datos para armar el payload UBL. La resolución `city_id` DANE→MATIAS es E2: aquí el cliente
llega con `city_id_matias` ya resuelto (o None → default en E1).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ItemFactura:
    """Una línea de la factura. `precio_unitario_con_iva` incluye IVA (como en BD): E1 extrae la base."""

    producto_id: int | None
    descripcion: str
    cantidad: Decimal
    precio_unitario_con_iva: Decimal
    pct_iva: Decimal
    unidad: str


@dataclass(frozen=True, slots=True)
class ClienteFiscal:
    """Datos fiscales del adquirente. Los 3 casos del §8.1 (consumidor final / NIT / persona) se
    discriminan en `ubl.armar_customer` a partir de `tipo_documento` y `numero` (no hay flag aparte).

    - `tipo_documento`: abreviatura (CC/CE/NIT/TI/PA/PPT/PEP…); "" o `numero` vacío/222222222222 = CF.
    - `numero`: documento sin el dv; para NIT puede venir como "900123456-5" (E1 separa dni y dv).
    - `dv`: dígito de verificación del NIT (obligatorio para NIT; se separa de `numero` si vino junto).
    - `regimen_fiscal`: 1=Responsable IVA / 2=No responsable (tolerar strings legados en E1).
    - `municipio_dian`: código DANE (string); `city_id_matias`/`city_name`: ciudad ya resuelta (E2/E3);
      el núcleo UBL es tenant-neutral y NO inventa default de ciudad si llegan vacíos.
    """

    tipo_documento: str = ""
    numero: str | None = None
    dv: str | None = None
    nombre: str = ""
    regimen_fiscal: int | str | None = None
    email: str | None = None
    mobile: str | None = None
    address: str | None = None
    municipio_dian: str | None = None
    country_id: int = 45
    city_id_matias: str | None = None
    city_name: str | None = None


@dataclass(frozen=True, slots=True)
class DatosEmision:
    """Cabecera de emisión: resolución/prefijo/consecutivo, fecha/hora y forma de pago."""

    resolution_number: str
    prefix: str
    document_number: str
    fecha: date
    hora: time
    means_payment_id: int
    payment_method_id: int
    notes: str


@dataclass(frozen=True, slots=True)
class FacturaInput:
    """Todo lo que el núcleo UBL necesita para armar el payload de una factura electrónica."""

    emision: DatosEmision
    cliente: ClienteFiscal
    items: list[ItemFactura]


@dataclass(frozen=True, slots=True)
class DatosEmisionPos:
    """Cabecera de emisión del POS electrónico (ADR 0012). MATIAS autoincrementa el `document_number`
    (D4), pero el `prefix` SÍ viaja: una misma `resolution_number` puede servir a varios tipos de
    documento y el endpoint la desambigua por prefijo (sin él responde 404). `prefix` None = no enviarlo."""

    resolution_number: str
    fecha: date
    hora: time
    means_payment_id: int
    payment_method_id: int
    notes: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class PuntoVenta:
    """Objeto `point_of_sale` del documento POS (todos obligatorios, ADR 0012 D5).

    `cashier_name` = vendedor de la venta; `terminal_number`/`address`/`cashier_type` = config de la
    empresa; `sales_code` = consecutivo interno de la venta; `sub_total` = total CON IVA calculado."""

    cashier_name: str
    terminal_number: str
    address: str
    cashier_type: str
    sales_code: str
    sub_total: Decimal


@dataclass(frozen=True, slots=True)
class SoftwareFabricante:
    """Bloque `software_manufacturer` del documento POS (exigido por `/auto-increment/pos-documents`).

    Identifica al fabricante del software emisor ante MATIAS/DIAN: `owner_name` (titular), `company_name`
    (razón social) y `software_name` (nombre del software). Datos NO secretos de la empresa (config).
    La FE NO lo lleva (solo el POS por autoincremento)."""

    owner_name: str
    company_name: str
    software_name: str


@dataclass(frozen=True, slots=True)
class PosInput:
    """Todo lo que el núcleo UBL necesita para armar el payload del documento equivalente POS."""

    emision: DatosEmisionPos
    cliente: ClienteFiscal
    items: list[ItemFactura]
    punto_venta: PuntoVenta
    software: SoftwareFabricante
