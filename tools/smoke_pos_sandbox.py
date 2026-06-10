"""Smoke de UN SOLO USO contra el sandbox MATIAS: confirma el SHAPE de la respuesta del documento
equivalente POS (type_document_id=20) por AUTOINCREMENTO y valida el parser real `_parsear_emision_pos`.

NO es un test ni parte del flujo: es una sonda manual para decidir, contra el sandbox, si la emisión
POS devuelve un estado FINAL síncrono (success + CUDE ≥40) o "en proceso" sin CUDE, y bajo qué claves
y anidamiento viajan el NÚMERO y el PREFIJO que MATIAS asigna. Reusa el código real de E1/E2 (no
reimplementa nada): `armar_payload_pos` (núcleo UBL), `MatiasClient.emitir_pos` y `_parsear_emision_pos`.

Credenciales SIEMPRE por args/env (nunca control DB ni secretos en código):
    python -m tools.smoke_pos_sandbox --email PR@... --password ***
    # o vía entorno
    $env:MATIAS_SMOKE_EMAIL="..."; $env:MATIAS_SMOKE_PASSWORD="..."; python -m tools.smoke_pos_sandbox

El bloque raíz `software_manufacturer` (exigido por el endpoint POS) se arma desde
--software-name/--company-name/--owner-name (o env MATIAS_SMOKE_SOFTWARE_NAME/COMPANY_NAME/OWNER_NAME);
la `resolution_number` POS activa de la cuenta va por --resolution y su prefijo por --prefix (o env
MATIAS_SMOKE_PREFIX). El prefijo es OBLIGATORIO: desambigua la resolución (sin él MATIAS da 404).

Emite DOS documentos POS en el sandbox (uno por el cliente real `emitir_pos`, otro por la llamada httpx
DIRECTA que vuelca headers/body). Es intencional y aceptable: es sandbox y es de un solo uso.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import time as time_cls
from decimal import Decimal

from core.config.timezone import now_co
from core.money import cuantizar
from modules.facturacion.matias_client import (
    MatiasClient,
    MatiasCredenciales,
    _a_json,
    _parsear_emision_pos,
)
from modules.facturacion.schemas import (
    ClienteFiscal,
    DatosEmisionPos,
    ItemFactura,
    PosInput,
    PuntoVenta,
    SoftwareFabricante,
)
from modules.facturacion.ubl import armar_payload_pos

_DEFAULT_BASE_URL = "https://sandbox-api.matias-api.com/api/ubl2.1"
# Resolución + prefijo POS verificados en el sandbox (10-jun): la resolución sirve a varios tipos y se
# desambigua por el prefijo DPOS (sin prefijo → 404). Sobreescribibles por args/env.
_DEFAULT_RESOLUTION = "18760000001"
_DEFAULT_PREFIX = "DPOS"
_ENDPOINT_POS = "/auto-increment/pos-documents"

# `software_manufacturer` por defecto (sobreescribible por args/env): identifica al software emisor.
_DEFAULT_SOFTWARE_NAME = "FerreBot"
_DEFAULT_COMPANY_NAME = "FerreBot SaaS"
_DEFAULT_OWNER_NAME = "FerreBot SaaS"

# Efectivo (núcleo §4): means_payment_id=10, payment_method_id=1 (contado).
_MEANS_EFECTIVO = 10
_PAYMENT_CONTADO = 1


def _construir_pos_input(resolution: str, software: SoftwareFabricante, prefix: str | None) -> PosInput:
    """Arma un `PosInput` representativo: consumidor final, efectivo, 2 ítems con IVA 19% incluido."""
    ahora = now_co()
    emision = DatosEmisionPos(
        resolution_number=resolution,
        prefix=prefix,                       # desambigua la resolución (sin él MATIAS da 404)
        fecha=ahora.date(),
        hora=time_cls(ahora.hour, ahora.minute, ahora.second),
        means_payment_id=_MEANS_EFECTIVO,
        payment_method_id=_PAYMENT_CONTADO,
        notes="SMOKE POS",
    )
    # Consumidor final: numero=222222222222 → `armar_customer` toma la rama CF.
    cliente = ClienteFiscal(numero="222222222222")
    items = [
        ItemFactura(
            producto_id=None,
            descripcion="TORNILLO 1/4 SMOKE",
            cantidad=Decimal("2"),
            precio_unitario_con_iva=Decimal("1190.00"),
            pct_iva=Decimal("19"),
            unidad="Unidad",
        ),
        ItemFactura(
            producto_id=None,
            descripcion="CINTA AISLANTE SMOKE",
            cantidad=Decimal("1"),
            precio_unitario_con_iva=Decimal("5950.00"),
            pct_iva=Decimal("19"),
            unidad="Unidad",
        ),
    ]
    # sub_total del punto de venta = total CON IVA de los ítems (lo que pagó el cliente).
    sub_total = cuantizar(
        sum((it.precio_unitario_con_iva * it.cantidad for it in items), Decimal("0.00"))
    )
    punto_venta = PuntoVenta(
        cashier_name="VENDEDOR PRUEBA",
        terminal_number="CJ01",
        address="BRR ALCIBIA CL 31 CR 30 72 P 1 LT 1",
        cashier_type="GENÉRICA",
        sales_code="POS-SMOKE-1",
        sub_total=sub_total,
    )
    return PosInput(
        emision=emision, cliente=cliente, items=items, punto_venta=punto_venta, software=software,
    )


def _dump(titulo: str, obj) -> None:
    """Imprime una sección claramente separada. Serializa Decimal como número (espejo de `_a_json`)."""
    print(f"\n{'=' * 78}\n{titulo}\n{'=' * 78}")
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, default=float, indent=2, ensure_ascii=False))
    else:
        print(obj)


async def _emitir_via_cliente(cliente: MatiasClient, payload: dict) -> None:
    """(a) payload, (b) respuesta CRUDA (raw), (c) parseado por `_parsear_emision_pos`. Robusto a error."""
    _dump("(a) PAYLOAD ENVIADO  →  emitir_pos()", payload)
    try:
        resultado = await cliente.emitir_pos(payload)
    except Exception as exc:  # noqa: BLE001 — smoke: jamás crashear, reportar y seguir
        _dump("(b/c) emitir_pos LANZÓ excepción", f"{type(exc).__name__}: {exc}")
        return
    _dump("(b) RESPUESTA CRUDA  (resultado.raw)", resultado.raw)
    _dump(
        "(c) PARSEADO por _parsear_emision_pos",
        {
            "ok": resultado.ok,
            "categoria": resultado.categoria,
            "cufe": resultado.cufe,
            "cufe_len": len(resultado.cufe) if resultado.cufe else 0,
            "numero": resultado.numero,
            "prefijo": resultado.prefijo,
            "error_msg": resultado.error_msg,
        },
    )


async def _emitir_directo(cliente: MatiasClient, payload: dict, base_url: str) -> None:
    """Llamada httpx DIRECTA al mismo endpoint: vuelca HEADERS de respuesta y BODY crudo completo.

    Reusa el token real del cliente (`_token`) para no abrir una segunda sesión de login. Confirma el
    header `X-MATIAS-Environment: sandbox` y el body exacto que recibe `_parsear_emision_pos`."""
    import httpx

    try:
        tok = await cliente._token()
        async with httpx.AsyncClient(base_url=base_url) as raw_client:
            resp = await raw_client.post(
                _ENDPOINT_POS,
                content=_a_json(payload),
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
    except Exception as exc:  # noqa: BLE001
        _dump("(directo) la llamada httpx LANZÓ excepción", f"{type(exc).__name__}: {exc}")
        return

    _dump(
        "(directo) STATUS + HEADERS de respuesta",
        {
            "http_status": resp.status_code,
            "X-MATIAS-Environment": resp.headers.get("X-MATIAS-Environment"),
            "headers": dict(resp.headers),
        },
    )
    _dump("(directo) BODY CRUDO COMPLETO", resp.text)


async def correr_con_input(cred: MatiasCredenciales, pos_input: PosInput) -> None:
    """Construye el payload con `armar_payload_pos` y corre las dos sondas; siempre cierra el cliente."""
    payload = armar_payload_pos(pos_input)
    cliente = MatiasClient(cred)
    try:
        await _emitir_via_cliente(cliente, payload)
        await _emitir_directo(cliente, payload, cred.base_url)
    finally:
        await cliente.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke POS sandbox MATIAS (autoincremento, shape de respuesta).")
    parser.add_argument("--email", default=os.environ.get("MATIAS_SMOKE_EMAIL"),
                        help="email MATIAS de Punto Rojo (o env MATIAS_SMOKE_EMAIL)")
    parser.add_argument("--password", default=os.environ.get("MATIAS_SMOKE_PASSWORD"),
                        help="password MATIAS de Punto Rojo (o env MATIAS_SMOKE_PASSWORD)")
    parser.add_argument("--base-url", default=_DEFAULT_BASE_URL,
                        help=f"base de la API MATIAS (default {_DEFAULT_BASE_URL})")
    parser.add_argument("--resolution", default=_DEFAULT_RESOLUTION,
                        help=f"resolution_number POS (default {_DEFAULT_RESOLUTION})")
    parser.add_argument("--prefix", default=os.environ.get("MATIAS_SMOKE_PREFIX", _DEFAULT_PREFIX),
                        help=f"prefijo POS que desambigua la resolución (default {_DEFAULT_PREFIX}, o env MATIAS_SMOKE_PREFIX)")
    parser.add_argument("--software-name",
                        default=os.environ.get("MATIAS_SMOKE_SOFTWARE_NAME", _DEFAULT_SOFTWARE_NAME),
                        help="software_manufacturer.software_name (o env MATIAS_SMOKE_SOFTWARE_NAME)")
    parser.add_argument("--company-name",
                        default=os.environ.get("MATIAS_SMOKE_COMPANY_NAME", _DEFAULT_COMPANY_NAME),
                        help="software_manufacturer.company_name (o env MATIAS_SMOKE_COMPANY_NAME)")
    parser.add_argument("--owner-name",
                        default=os.environ.get("MATIAS_SMOKE_OWNER_NAME", _DEFAULT_OWNER_NAME),
                        help="software_manufacturer.owner_name (o env MATIAS_SMOKE_OWNER_NAME)")
    args = parser.parse_args(argv)

    # La consola Windows (cp1252) no codifica tildes ni '→'; forzar UTF-8 para volcar el body crudo.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if not args.email or not args.password:
        print("error: faltan credenciales — pasa --email/--password o MATIAS_SMOKE_EMAIL/MATIAS_SMOKE_PASSWORD",
              file=sys.stderr)
        return 2

    cred = MatiasCredenciales(email=args.email, password=args.password, base_url=args.base_url)
    software = SoftwareFabricante(
        owner_name=args.owner_name, company_name=args.company_name, software_name=args.software_name,
    )
    pos_input = _construir_pos_input(args.resolution, software, args.prefix or None)
    asyncio.run(correr_con_input(cred, pos_input))
    return 0


if __name__ == "__main__":
    sys.exit(main())
