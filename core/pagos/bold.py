"""Adaptador Bold (ADR 0013): link de pago con QR Bre-B + tarjetas/PSE/Nequi, vía su API pública.

Contrato (https://developers.bold.co/pagos-en-linea/api-link-de-pagos):
  - Auth: header `Authorization: x-api-key <llave_de_identidad>` (la llave del Botón de Pagos).
  - `POST /online/link/v1` crea el link: `amount_type=CLOSE` + `amount{currency, total_amount}` +
    `reference` (≤60 chars, nuestra llave idempotente) + `description` + `expiration_date`
    (epoch en NANOSEGUNDOS).
  - `GET /online/link/v1/{payment_link}` consulta: `ACTIVE | PROCESSING | PAID | REJECTED |
    CANCELLED | EXPIRED`.

El estado se normaliza al vocabulario del puerto. La llave es POR TENANT (cifrada en el control DB):
el dinero va a la Bold Account del negocio, jamás a la plataforma. Cliente httpx inyectable (tests);
en producción se crea perezoso por llamada (sin red al importar).

NOTA: el webhook de Bold existe pero su firma/formato no están en la doc pública → v1 concilia por
POLLING (cron del worker, patrón reconciliar_pendientes de MATIAS). Webhook = v1.1 con la cuenta abierta.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.pagos.ports import EstadoCobro, LinkCobro, SolicitudCobro

BASE_URL_DEFAULT = "https://integrations.api.bold.co"

# Estado Bold → estado normalizado del puerto.
_ESTADOS: dict[str, EstadoCobro] = {
    "ACTIVE": "pendiente",
    "PROCESSING": "pendiente",
    "PAID": "pagado",
    "REJECTED": "cancelado",
    "CANCELLED": "cancelado",
    "EXPIRED": "vencido",
}


class ErrorBold(Exception):
    """Respuesta inesperada de Bold (status no-2xx o shape desconocido)."""


@dataclass(frozen=True, slots=True)
class BoldCredenciales:
    """La llave de identidad del comercio (POR TENANT, descifrada del control DB) + base URL."""

    api_key: str
    base_url: str = BASE_URL_DEFAULT


class BoldClient:
    def __init__(
        self, cred: BoldCredenciales, *, client: Any | None = None, timeout: float = 15.0
    ) -> None:
        self._cred = cred
        self._client = client
        self._timeout = timeout

    async def crear_link(self, solicitud: SolicitudCobro) -> LinkCobro:
        cuerpo: dict[str, Any] = {
            "amount_type": "CLOSE",
            "amount": {"currency": "COP", "total_amount": float(solicitud.monto), "tip_amount": 0},
            "reference": solicitud.referencia[:60],
            "description": solicitud.descripcion[:100],
        }
        if solicitud.vence_en is not None:
            cuerpo["expiration_date"] = int(solicitud.vence_en.timestamp() * 1_000_000_000)
        data = await self._request("POST", "/online/link/v1", json=cuerpo)
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        link = payload.get("payment_link")
        url = payload.get("url")
        if not link or not url:
            raise ErrorBold(f"respuesta sin payment_link/url: claves={sorted(payload)}")
        return LinkCobro(proveedor_id=str(link), url=str(url))

    async def consultar(self, proveedor_id: str) -> EstadoCobro:
        data = await self._request("GET", f"/online/link/v1/{proveedor_id}")
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        crudo = str(payload.get("status") or "").upper()
        estado = _ESTADOS.get(crudo)
        if estado is None:
            raise ErrorBold(f"status desconocido: {crudo!r}")
        return estado

    async def _request(self, metodo: str, path: str, *, json: dict | None = None) -> dict:
        url = f"{self._cred.base_url.rstrip('/')}{path}"
        headers = {"Authorization": f"x-api-key {self._cred.api_key}"}
        if self._client is not None:
            resp = await self._client.request(metodo, url, json=json, headers=headers)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=self._timeout) as cliente:
                resp = await cliente.request(metodo, url, json=json, headers=headers)
        if resp.status_code // 100 != 2:
            # Cuerpo truncado y sin secretos: solo para diagnóstico.
            raise ErrorBold(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise ErrorBold("respuesta no es un objeto JSON")
        return data
