"""Cliente OAuth2 + Gmail API para la ingesta bancaria — TENANT-AGNÓSTICO (port del legacy).

Recibe credenciales (no las resuelve): quien lo instancia trae `client_id/secret` de plataforma y el
`refresh_token` del tenant (descifrado del control DB). Expone `history.list`, `messages.get`
(metadata/full) y `watch`. El access_token se cachea en memoria por refresh_token (1h, margen 5 min).

Diseñado para que la futura ingesta de facturas de compra por correo (nota D2) reuse el MISMO cliente:
es genérico de Gmail, no sabe de Bancolombia.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from core.logging import get_logger

log = get_logger("bancos.gmail.cliente")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://gmail.googleapis.com/gmail/v1"


class RefreshTokenInvalido(RuntimeError):
    """Google rechazó el refresh_token (400): revocado o expirado. El caller alerta al tenant."""


@dataclass(slots=True)
class _AccessCache:
    token: str
    expira_monotonic: float


class GmailCliente:
    def __init__(self, *, client_id: str, client_secret: str, refresh_token: str,
                 usuario: str = "me", http: httpx.AsyncClient | None = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._usuario = usuario
        self._http = http
        self._cache: _AccessCache | None = None
        self._lock = asyncio.Lock()
        # Si Google rota el refresh_token, se expone aquí para que el caller lo re-cifre y persista.
        self.refresh_token_rotado: str | None = None

    async def _cliente(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=20)

    async def _pedido(self, method: str, url: str, **kw) -> httpx.Response:
        if self._http is not None:
            return await self._http.request(method, url, **kw)
        async with httpx.AsyncClient(timeout=20) as c:
            return await c.request(method, url, **kw)

    async def access_token(self) -> str:
        """Access token vigente (cacheado). Renueva vía refresh_token; rota si Google emite uno nuevo."""
        async with self._lock:
            ahora = time.monotonic()
            if self._cache and ahora < self._cache.expira_monotonic:
                return self._cache.token
            resp = await self._pedido("POST", _TOKEN_URL, data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            })
            if resp.status_code == 400:
                log.error("gmail_refresh_invalido", detalle=resp.text[:300])
                raise RefreshTokenInvalido(resp.text[:300])
            resp.raise_for_status()
            data = resp.json()
            expira_in = int(data.get("expires_in", 3600))
            self._cache = _AccessCache(token=data["access_token"],
                                       expira_monotonic=ahora + expira_in - 300)
            nuevo = data.get("refresh_token", "")
            if nuevo and nuevo != self._refresh_token:
                log.info("gmail_refresh_rotado")
                self._refresh_token = nuevo
                self.refresh_token_rotado = nuevo
            return self._cache.token

    async def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.access_token()}"}

    async def ids_desde_history(self, history_id: str) -> list[str]:
        """IDs de mensajes agregados al INBOX desde `history_id` (history.list). [] si expiró (404)."""
        resp = await self._pedido(
            "GET", f"{_API_BASE}/users/{self._usuario}/history",
            headers=await self._auth(),
            params={"startHistoryId": history_id, "historyTypes": "messageAdded"},
        )
        if resp.status_code == 404:
            log.warning("gmail_history_expirado", history_id=history_id)
            return []
        resp.raise_for_status()
        ids: list[str] = []
        for entry in resp.json().get("history", []):
            for msg in entry.get("messagesAdded", []):
                mid = msg.get("message", {}).get("id")
                if mid and mid not in ids:
                    ids.append(mid)
        return ids

    async def headers(self, message_id: str) -> list[dict]:
        """Solo headers From/Subject (format=metadata, ~10ms). [] si falla."""
        try:
            resp = await self._pedido(
                "GET", f"{_API_BASE}/users/{self._usuario}/messages/{message_id}",
                headers=await self._auth(),
                params=[("format", "metadata"), ("metadataHeaders", "From"),
                        ("metadataHeaders", "Subject")],
            )
            resp.raise_for_status()
            return resp.json().get("payload", {}).get("headers", [])
        except Exception:
            log.warning("gmail_headers_fallo", message_id=message_id, exc_info=True)
            return []

    async def mensaje_completo(self, message_id: str) -> dict | None:
        """Mensaje completo (payload con body). None si falla."""
        try:
            resp = await self._pedido(
                "GET", f"{_API_BASE}/users/{self._usuario}/messages/{message_id}",
                headers=await self._auth(), params={"format": "full"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            log.warning("gmail_mensaje_fallo", message_id=message_id, exc_info=True)
            return None

    async def watch(self, topic: str) -> dict:
        """(Re)activa el watch sobre INBOX hacia el topic Pub/Sub. Devuelve {historyId, expiration}."""
        resp = await self._pedido(
            "POST", f"{_API_BASE}/users/{self._usuario}/watch",
            headers={**await self._auth(), "Content-Type": "application/json"},
            json={"labelIds": ["INBOX"], "topicName": topic},
        )
        resp.raise_for_status()
        return resp.json()
