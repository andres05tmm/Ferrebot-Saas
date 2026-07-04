"""Decodificación del contenido de un QR de factura electrónica DIAN → CUFE (ADR 0020, F1).

Función PURA (sin IO): recibe el TEXTO ya leído del QR y extrae el CUFE. Tres formas soportadas
(Pregunta abierta #5 del ADR: el QR puede traer el CUFE directo o una URL DIAN):

  1. **URL DIAN** — `https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=<CUFE>`: se toma
     el parámetro `documentkey` (case-insensitive).
  2. **Campos clave=valor** — el texto contiene `CUFE:<hash>` / `CUFE=<hash>` (o `CUDE`).
  3. **CUFE crudo** — el QR ES el hash hex (SHA-384 → 96 hex; se admite un rango tolerante).

El CUFE se normaliza a minúsculas. Si no se reconoce ninguna forma, se lanza `QRInvalido` (el router lo
mapea a 422): jamás se inventa un CUFE. Decodificar la IMAGEN a texto es de otra capa (F2: bot/dashboard).
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from modules.compras_fiscal.errors import QRInvalido

# CUFE/CUDE = hash hex. El SHA-384 son 96 hex; se tolera 40..128 para no rechazar variantes válidas.
_HEX_CUFE = re.compile(r"^[0-9a-fA-F]{40,128}$")
# `CUFE:<hex>` o `CUFE=<hex>` (también CUDE) embebido en un texto de campos.
_CAMPO_CUFE = re.compile(r"(?:CUFE|CUDE)\s*[:=]\s*([0-9a-fA-F]{40,128})", re.IGNORECASE)


def extraer_cufe(qr: str) -> str:
    """Extrae y normaliza (minúsculas) el CUFE del contenido de un QR. `QRInvalido` si no se reconoce."""
    texto = (qr or "").strip()
    if not texto:
        raise QRInvalido("El contenido del QR está vacío")

    # 1) URL DIAN con `documentkey`.
    if "://" in texto or texto.lower().startswith("www."):
        cufe = _cufe_de_url(texto)
        if cufe:
            return cufe.lower()

    # 2) Campos clave=valor con CUFE/CUDE.
    campo = _CAMPO_CUFE.search(texto)
    if campo:
        return campo.group(1).lower()

    # 3) CUFE crudo (el QR es el hash).
    if _HEX_CUFE.match(texto):
        return texto.lower()

    raise QRInvalido("No se pudo extraer el CUFE del contenido del QR")


def _cufe_de_url(texto: str) -> str | None:
    """CUFE del parámetro `documentkey` de una URL DIAN (claves case-insensitive), o None."""
    try:
        parsed = urlparse(texto if "://" in texto else f"https://{texto}")
        params = parse_qs(parsed.query)
    except ValueError:
        return None
    for clave, valores in params.items():
        if clave.lower() == "documentkey" and valores:
            candidato = valores[0].strip()
            if _HEX_CUFE.match(candidato):
                return candidato
    return None
