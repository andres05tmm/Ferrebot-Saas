"""Set-password y reset por token de un solo uso (login real, ADR 0009 §D3, A1.3).

Las identidades se crean SIN contraseña (`password_hash` NULL); el usuario la establece por un enlace
con token. Tokens de UN SOLO USO con expiración, guardados en Redis bajo `sha256(token)` (NUNCA el token
en claro: la clave es el hash, el valor es el `identidad_id`, el TTL es la expiración). Consumir es
atómico (GETDEL) → reuso imposible. `set-password` y `reset/confirmar` son la MISMA operación
(token → nueva contraseña); `reset/solicitar` genera el token. El token NUNCA se loguea (es un secreto:
solo viaja al usuario); el envío de email real es un TODO aparte. SIN enumeración en `reset/solicitar`:
200 genérico exista o no el email, y rate-limit en TRES cubos INDEPENDIENTES (Redis INCR+EXPIRE): por
email solo (inmune a la rotación de IP → frena email-bombing dirigido), por IP sola (último salto de
XFF, el del proxy) y GLOBAL (tope total del servicio → frena el bombing masivo distribuido). Todos
suben el contador ANTES de tocar el directorio → el 429 tampoco revela si el email existe.

Rutas eximidas del TenantMiddleware (`_AUTH_SIN_TENANT`): el flujo ocurre sobre el link compartido, sin
empresa resuelta. Token store, repo (control DB) y rate-limiter se inyectan → testeable sin red.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.auth.passwords import hash_password
from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.identidades_repo import Identidad
from core.tenancy.identidades_repo import buscar_por_email as _repo_buscar
from core.tenancy.identidades_repo import set_password_hash as _repo_set_hash

log = get_logger("auth")
router = APIRouter(tags=["auth"])

_MIN_PASSWORD = 8   # política mínima de contraseña (longitud)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clave_pwtoken(token: str) -> str:
    """Clave Redis de un token de set-password/reset: `sha256(token)` (nunca el token en claro).

    Único lugar con el formato; lo reusa también el provisionador (que emite el token desde código
    SYNC) para que el endpoint async lo consuma con la misma clave. Cambiarlo aquí los mantiene en sync.
    """
    return f"auth:pwtoken:{_sha(token)}"


# --- Puertos inyectables (testeo sin red) -----------------------------------
class TokenStore(Protocol):
    """Tokens de un solo uso con TTL. `consumir` los invalida (single-use atómico)."""

    async def crear(self, identidad_id: int, ttl_segundos: int) -> str: ...
    async def consumir(self, token: str) -> int | None: ...


class RepoIdentidades(Protocol):
    async def buscar_por_email(self, email: str) -> Identidad | None: ...
    async def set_password_hash(self, identidad_id: int, password_hash: str) -> None: ...


class RateLimiter(Protocol):
    """Rate-limit por clave (IP+email). `permitido` cuenta el intento y dice si sigue bajo el tope."""

    async def permitido(self, clave: str) -> bool: ...


class _RedisTokenStore:
    """Guarda `sha256(token) → identidad_id` con TTL; `consumir` = GETDEL (un solo uso)."""

    def __init__(self, client: Any) -> None:
        self._c = client

    async def crear(self, identidad_id: int, ttl_segundos: int) -> str:
        token = secrets.token_urlsafe(32)
        await self._c.set(clave_pwtoken(token), str(identidad_id), ex=ttl_segundos)
        return token

    async def consumir(self, token: str) -> int | None:
        valor = await self._c.getdel(clave_pwtoken(token))   # atómico: leer + borrar (single-use)
        return int(valor) if valor is not None else None


class _RedisRateLimiter:
    """Contador por clave con TTL = ventana (INCR+EXPIRE); bloquea cuando el contador pasa el tope.

    Espeja el lockout de login (`modules/auth/login_email._RedisLockout`): el primer intento de la
    ventana fija el EXPIRE; los siguientes solo incrementan. `permitido` devuelve False cuando el
    contador supera `max_intentos` → el handler responde 429.
    """

    def __init__(self, client: Any, max_intentos: int, ventana_s: int, cubo: str) -> None:
        self._c = client
        self._max = max_intentos
        self._ventana = ventana_s
        self._cubo = cubo            # namespace del cubo ("email"/"ip"): cubos independientes en Redis

    def _key(self, clave: str) -> str:
        return f"auth:reset:rl:{self._cubo}:{clave}"

    async def permitido(self, clave: str) -> bool:
        key = self._key(clave)
        n = await self._c.incr(key)
        if n == 1:                       # primer intento de la ventana → fija el TTL de expiración
            await self._c.expire(key, self._ventana)
        return n <= self._max


class _RepoControl:
    """Repo real: abre una sesión de control FRESCA por llamada (set_password_hash commitea)."""

    async def buscar_por_email(self, email: str) -> Identidad | None:
        async with control_session() as cs:
            return await _repo_buscar(cs, email)

    async def set_password_hash(self, identidad_id: int, password_hash: str) -> None:
        async with control_session() as cs:
            await _repo_set_hash(cs, identidad_id, password_hash)
            await cs.commit()


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar (patrón del bot/wa)."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


def get_token_store() -> TokenStore:
    return _RedisTokenStore(_cliente_redis(get_settings().redis_url))


def get_repo_identidades() -> RepoIdentidades:
    return _RepoControl()


_RESET_GLOBAL_MAX_INTENTOS = 200   # tope TOTAL de solicitudes de reset por ventana (todas las fuentes)
_RESET_GLOBAL_VENTANA_S = 900
_RESET_CLAVE_GLOBAL = "todas"      # clave única del cubo global (un solo contador para todo el servicio)


def get_rate_limiters() -> tuple[RateLimiter, RateLimiter, RateLimiter]:
    """Tres cubos INDEPENDIENTES para `reset/solicitar`: (por-email, por-IP, global). El handler
    dispara 429 si CUALQUIERA pasa su tope. Separarlos cierra el bypass del cubo combinado {ip}:{email}:
    rotar la IP (XFF spoofeable) ya no abre un cubo nuevo por intento (el de email es inmune a la IP),
    y una IP que rota emails topa contra el cubo de IP. El GLOBAL (clave única) es la red de seguridad
    contra el email-bombing masivo distribuido: rotar IPs Y emails a la vez topa igual con el total."""
    s = get_settings()
    cliente = _cliente_redis(s.redis_url)   # un cliente, tres cubos (namespaces distintos en la clave)
    por_email = _RedisRateLimiter(
        cliente, s.reset_solicitar_max_intentos, s.reset_solicitar_ventana_segundos, cubo="email"
    )
    por_ip = _RedisRateLimiter(
        cliente, s.reset_solicitar_ip_max_intentos, s.reset_solicitar_ip_ventana_segundos, cubo="ip"
    )
    global_ = _RedisRateLimiter(cliente, _RESET_GLOBAL_MAX_INTENTOS, _RESET_GLOBAL_VENTANA_S, cubo="global")
    return por_email, por_ip, global_


def client_ip(request: Request) -> str:
    """IP del cliente para rate-limit/lockout (la comparte el login, `modules/auth/login_email`).

    Se toma el ÚLTIMO elemento de X-Forwarded-For: es el que APPENDEA el proxy de confianza (Railway)
    y el único que el cliente no controla. El primer salto es spoofeable (el atacante manda el header
    que quiera y abriría un cubo nuevo por request); el último no. Sin header, cae al peer de la
    conexión."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        saltos = [p.strip() for p in xff.split(",") if p.strip()]
        if saltos:
            return saltos[-1]
    return request.client.host if request.client else "desconocida"


# --- Schemas ----------------------------------------------------------------
class SetPassword(BaseModel):
    """Body de set-password / reset/confirmar. Política mínima: longitud >= 8."""

    token: str
    password: str = Field(min_length=_MIN_PASSWORD)


class SolicitarReset(BaseModel):
    email: str


# --- Lógica compartida ------------------------------------------------------
async def _aplicar_password(datos: SetPassword, store: TokenStore, repo: RepoIdentidades) -> dict:
    """Consume el token (un solo uso) y fija la contraseña. 400 si el token no vale."""
    identidad_id = await store.consumir(datos.token)
    if identidad_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Token inválido o expirado")
    await repo.set_password_hash(identidad_id, hash_password(datos.password))
    log.info("password_establecida", identidad_id=identidad_id)   # nunca la clave/hash en el log
    return {"detail": "Contraseña actualizada."}


# --- Endpoints --------------------------------------------------------------
@router.post("/auth/set-password")
async def set_password(
    datos: SetPassword,
    store: TokenStore = Depends(get_token_store),
    repo: RepoIdentidades = Depends(get_repo_identidades),
) -> dict:
    """Establece la contraseña de una identidad (creada sin clave) a partir del token del enlace."""
    return await _aplicar_password(datos, store, repo)


@router.post("/auth/reset/confirmar")
async def reset_confirmar(
    datos: SetPassword,
    store: TokenStore = Depends(get_token_store),
    repo: RepoIdentidades = Depends(get_repo_identidades),
) -> dict:
    """Confirma el reset: igual que set-password (token → nueva contraseña)."""
    return await _aplicar_password(datos, store, repo)


@router.post("/auth/reset/solicitar")
async def reset_solicitar(
    datos: SolicitarReset,
    request: Request,
    store: TokenStore = Depends(get_token_store),
    repo: RepoIdentidades = Depends(get_repo_identidades),
    limiters: tuple[RateLimiter, RateLimiter, RateLimiter] = Depends(get_rate_limiters),
) -> dict:
    """Solicita un reset. Anti-abuso/anti-enumeración:
    - Rate-limit en TRES cubos INDEPENDIENTES (Redis): por email solo (sha(email), inmune a la rotación
      de IP → frena el email-bombing dirigido), por IP sola (la del proxy: último salto de XFF → frena
      el abuso de una IP rotando emails) y GLOBAL (clave única → frena el email-bombing masivo aunque
      roten IPs y emails). 429 si CUALQUIERA pasa su tope. TODOS cuentan ANTES de tocar el directorio
      → el 429 no depende de si el email existe (no enumera).
    - SIN enumeración: 200 genérico exista o no el email. Si existe, genera el token de un solo uso.
    El token NUNCA se loguea (es un secreto); el envío por email real es un TODO aparte."""
    email = datos.email.strip().lower()
    ip = client_ip(request)
    rl_email, rl_ip, rl_global = limiters
    # Cuenta TODOS los cubos SIEMPRE (sin cortocircuito): mismas operaciones exista o no el email.
    ok_email = await rl_email.permitido(_sha(email))   # cubo email: clave sha(email), SIN IP
    ok_ip = await rl_ip.permitido(ip)                  # cubo IP: clave IP sola
    ok_global = await rl_global.permitido(_RESET_CLAVE_GLOBAL)   # cubo global: un contador para todo
    if not (ok_email and ok_ip and ok_global):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiadas solicitudes. Intenta más tarde.")

    identidad = await repo.buscar_por_email(datos.email)
    if identidad is not None:
        token = await store.crear(identidad.id, get_settings().auth_token_ttl_segundos)
        # Solo una referencia NO reversible (prefijo del hash) para trazar la emisión; jamás el token.
        log.info("reset_token_generado", identidad_id=identidad.id, token_ref=_sha(token)[:12])
    return {"detail": "Si el email existe, te enviaremos un enlace para restablecer la contraseña."}
