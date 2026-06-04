"""Cliente MATIAS API v3 por empresa (un cliente por tenant; caché en la instancia).

Aísla el borde HTTP con MATIAS (auth JWT, emisión de factura, caché de ciudades) tras una clase
inyectable, con httpx PEREZOSO: importar este módulo y construir `MatiasClient` NO toca la red
(patrón CR-1, igual que `apps/bot/telegram.py` y `apps/bot/redis_stores.py`). Los parsers son
PUROS (sin red) y se prueban aislados; la orquestación se prueba con `httpx.MockTransport`.

Contrato MATIAS en `docs/facturacion-matias-extract.md` §2, §5, §10, §11 (portado de
`bot-ventas-ferreteria/services/facturacion_service.py`). La arquitectura cambia: credenciales
inyectadas por empresa y caché por instancia (no env global). Alcance E2 = SOLO el cliente; la
persistencia (ventas/`facturas_electronicas`) es E3.

`base_url` es ÚNICO: auth y API cuelgan del mismo host (`{base}/auth/login`, `{base}/invoice`,
`{base}/cities`), como en `facturacion_service.py:307`. El host `auth-v2` del doc §2 es stale.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

# CUFE mínimo válido (FAD06, §9): `success` sin CUFE de ≥40 chars se trata como fallo.
CUFE_MIN_LEN = 40
# Expiración por defecto del token si MATIAS no informa `expires_at`/`expires_in` (segundos, §2).
_EXPIRY_DEFAULT = 86_400
# Margen para renovar el token antes de que expire (segundos, §2).
_EXPIRY_MARGEN = 60


@dataclass(frozen=True, slots=True)
class MatiasCredenciales:
    """Credenciales MATIAS de UNA empresa (descifradas en memoria por job; nunca en código/git)."""

    email: str
    password: str
    base_url: str


@dataclass(frozen=True, slots=True)
class EmisionResultado:
    """Resultado de emitir un documento: `cufe` en éxito, `error_msg` legible en fallo.

    `categoria` clasifica el desenlace para la política de reintento (E4): "aceptada" | "rechazada"
    | "error". El default "error" es un placeholder; `_parsear_emision` la fija siempre en GREEN
    (E4b actualizará el servicio para consumirla).
    """

    ok: bool
    cufe: str | None = None
    error_msg: str | None = None
    categoria: str = "error"


# --- parsers PUROS (sin red) -------------------------------------------------

def _extraer_token(data: dict, *, ahora: float) -> tuple[str, float]:
    """Token + timestamp de expiración desde la respuesta de `/auth/login` (§2). PURO.

    Token en `token` | `access_token` | `data.token` | `data.access_token` (ninguno → ValueError).
    Expiry: `expires_at` ISO ("Z"→"+00:00") o `ahora + expires_in` (default `_EXPIRY_DEFAULT`).
    """
    anidado = data.get("data") or {}
    token = data.get("token") or data.get("access_token") or anidado.get("token") or anidado.get("access_token")
    if not token:
        raise ValueError("Respuesta de login sin token MATIAS")
    expires_at = data.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
    else:
        expiry = ahora + float(data.get("expires_in") or _EXPIRY_DEFAULT)
    return token, expiry


def _parsear_emision(data: dict) -> EmisionResultado:
    """Parsea la respuesta de `/invoice` (§10) con pre-check FAD06 (§9). PURO.

    Éxito = `success` y CUFE (`XmlDocumentKey`|`document_key`) de ≥`CUFE_MIN_LEN` chars; si falta o es
    corto → fallo. En rechazo concatena `message` con `errors` (dict) en un `error_msg` legible.
    """
    cufe = (data.get("XmlDocumentKey") or data.get("document_key") or "").strip()
    if bool(data.get("success")):
        if not cufe or len(cufe) < CUFE_MIN_LEN:
            return EmisionResultado(False, error_msg="CUFE inválido devuelto por MATIAS API", categoria="error")
        return EmisionResultado(True, cufe=cufe, categoria="aceptada")
    msg = data.get("message") or ""
    errors = data.get("errors")
    if isinstance(errors, dict) and errors:
        error_msg = f"{msg} | " + " | ".join(f"{k}: {v}" for k, v in errors.items())
    else:
        error_msg = msg or str(data)
    return EmisionResultado(False, error_msg=error_msg, categoria="rechazada")


def _parsear_ciudades(data: dict) -> dict[int, str]:
    """Construye {dane_code:int → matias_id:str} desde `/cities` (§5). PURO.

    Códigos en `dataRecords.data` o `data`; cada `code`|`dane_code`|`municipality_code` mapea a
    `str(id)`. Entradas inválidas (code no numérico, sin `id`, None) se saltan.
    """
    cities = (data.get("dataRecords", {}) or {}).get("data", []) or data.get("data", []) or []
    resultado: dict[int, str] = {}
    for city in cities:
        code = city.get("code") or city.get("dane_code") or city.get("municipality_code")
        try:
            resultado[int(str(code))] = str(city["id"])
        except (ValueError, KeyError, TypeError):
            continue
    return resultado


# --- cliente por empresa (httpx perezoso) ------------------------------------

class MatiasClient:
    """Cliente MATIAS de UNA empresa: auth con caché, emisión y caché de ciudades (todo perezoso)."""

    def __init__(self, cred: MatiasCredenciales, *, client: httpx.AsyncClient | None = None) -> None:
        """Guarda credenciales y cliente inyectado; NO crea httpx ni toca red. Caché vacía (CR-1)."""
        self._cred = cred
        self._client = client
        self._token_val: str | None = None
        self._token_expiry: float = 0.0
        self._ciudades: dict[int, str] | None = None
        self._token_lock = asyncio.Lock()
        self._ciudades_lock = asyncio.Lock()

    def _get_client(self) -> httpx.AsyncClient:
        """Cliente httpx perezoso y MEMOIZADO (uno por instancia atado al `base_url`); no abre red."""
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._cred.base_url)
        return self._client

    async def _token(self) -> str:
        """JWT con caché por instancia, renovado `_EXPIRY_MARGEN` s antes de expirar; login perezoso (§2)."""
        async with self._token_lock:
            ahora = time.time()
            if self._token_val and ahora < self._token_expiry - _EXPIRY_MARGEN:
                return self._token_val
            resp = await self._get_client().post(
                "/auth/login",
                json={"email": self._cred.email, "password": self._cred.password, "remember_me": 0},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                follow_redirects=True, timeout=15,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError as exc:
                raise ValueError("Respuesta de login MATIAS vacía o no-JSON") from exc
            self._token_val, self._token_expiry = _extraer_token(data, ahora=ahora)
            return self._token_val

    async def emitir_factura(self, payload: dict) -> EmisionResultado:
        """POST `/invoice` con Bearer token; devuelve `EmisionResultado` (NO persiste; eso es E3, §7/§10)."""
        tok = await self._token()
        resp = await self._get_client().post(
            "/invoice", json=payload,
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=30,
        )
        return _parsear_emision(resp.json())

    async def city_id(self, dane_code: str | int | None) -> str | None:
        """Resuelve código DANE → id MATIAS de ciudad (caché perezosa por instancia, §5)."""
        if dane_code is None:
            return None
        async with self._ciudades_lock:
            if self._ciudades is None:
                resp = await self._get_client().get("/cities", timeout=15)
                self._ciudades = _parsear_ciudades(resp.json())
        try:
            return self._ciudades.get(int(dane_code))
        except (ValueError, TypeError):
            return None

    async def aclose(self) -> None:
        """Cierra el cliente httpx si existe (lifecycle lo maneja la capa superior en E3)."""
        if self._client is not None:
            await self._client.aclose()
