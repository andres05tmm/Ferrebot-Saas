"""Login real email/contraseña sobre link compartido (ADR 0009 §D2). Convive con el login Telegram.

El flujo se INVIERTE respecto al de Telegram: aquí NO hay tenant resuelto (el middleware exime esta
ruta, ver core/tenancy/middleware._AUTH_SIN_TENANT); autenticamos primero y el tenant SALE del usuario:
la identidad (control DB, A1.1) trae `empresa_id` → `slug` → claim `tenant` del JWT, reusando
`create_access_token`. Va en `/auth/login/password` para no chocar con el `/auth/login` de Telegram.

Seguridad (ADR 0009 §D4):
- SIN enumeración de usuarios: mismo 401 genérico y costo temporal SIMILAR para email inexistente,
  clave errada, identidad inactiva o sin contraseña aún (se verifica un hash DUMMY para igualar el
  tiempo de argon2 cuando no hay hash real). Nunca se ramifica el status/mensaje por la causa.
- Lockout en DOS cubos INDEPENDIENTES en Redis: por email (N fallos/ventana, configurable) y por IP
  (tope generoso para NAT/oficinas compartidas) → frena el password-spraying contra muchos emails desde
  una misma IP. 429 si CUALQUIERA está en el tope. La IP sale del último salto de X-Forwarded-For (el
  del proxy, no spoofeable; `modules/auth/password_reset.client_ip`). El éxito resetea SOLO el cubo de
  email: el de IP expira por TTL (resetearlo dejaría que un spraying con una credencial válida a mano
  limpiara su contador).
- Hashes/secretos jamás en logs.

El directorio (control DB) y el lockout (Redis) se inyectan por dependencia → testeable sin red.
"""
from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from core.auth import create_access_token, create_platform_token
from core.auth.passwords import hash_password, verify_password
from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.identidades_repo import Identidad, buscar_por_email
from modules.auth.password_reset import client_ip
from modules.auth.router import LoginOut, UsuarioOut

log = get_logger("auth")

router = APIRouter(tags=["auth"])

# Cubo por IP (anti password-spraying): generoso a propósito —una IP de NAT/oficina agrupa a muchos
# usuarios legítimos— pero suficiente para cortar el spraying sostenido desde una sola IP.
_LOGIN_IP_MAX_INTENTOS = 30
_LOGIN_IP_VENTANA_S = 900

# Hash DUMMY (argon2id) para igualar el costo temporal cuando NO hay hash real (email inexistente o
# identidad sin contraseña). Se computa una vez al cargar el módulo; su valor exacto es irrelevante.
_DUMMY_HASH = hash_password("tiempo-constante-no-es-una-credencial")


class EmailLogin(BaseModel):
    """Body de POST /auth/login/password (sin tenant: el tenant sale del usuario)."""

    email: str
    password: str


# --- Puertos inyectables (testeo sin red) -----------------------------------
class Directorio(Protocol):
    """Acceso de solo-lectura al directorio de identidades + slug de la empresa (control DB)."""

    async def buscar(self, email: str) -> Identidad | None: ...
    async def slug_empresa(self, empresa_id: int) -> str | None: ...


class Lockout(Protocol):
    """Rate-limit/lockout por clave (email) en Redis."""

    async def bloqueado(self, clave: str) -> bool: ...
    async def registrar_fallo(self, clave: str) -> None: ...
    async def reset(self, clave: str) -> None: ...


class _DirectorioControl:
    """Implementación real: abre una sesión de control FRESCA por llamada."""

    async def buscar(self, email: str) -> Identidad | None:
        async with control_session() as cs:
            return await buscar_por_email(cs, email)

    async def slug_empresa(self, empresa_id: int) -> str | None:
        async with control_session() as cs:
            row = (
                await cs.execute(text("SELECT slug FROM empresas WHERE id = :id"), {"id": empresa_id})
            ).first()
            return row[0] if row else None


class _RedisLockout:
    """Lockout con un contador por email y TTL = ventana; bloqueado cuando el contador llega al tope."""

    def __init__(self, client: Any, max_intentos: int, ventana_s: int) -> None:
        self._c = client
        self._max = max_intentos
        self._ventana = ventana_s

    @staticmethod
    def _key(clave: str) -> str:
        return f"login:fail:{clave}"

    async def bloqueado(self, clave: str) -> bool:
        valor = await self._c.get(self._key(clave))
        return valor is not None and int(valor) >= self._max

    async def registrar_fallo(self, clave: str) -> None:
        key = self._key(clave)
        n = await self._c.incr(key)
        if n == 1:                       # primer fallo de la ventana → fija el TTL de expiración
            await self._c.expire(key, self._ventana)

    async def reset(self, clave: str) -> None:
        await self._c.delete(self._key(clave))


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar (patrón del bot/wa)."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


def get_directorio() -> Directorio:
    return _DirectorioControl()


def get_lockout() -> Lockout:
    s = get_settings()
    return _RedisLockout(_cliente_redis(s.redis_url), s.login_max_intentos, s.login_lockout_segundos)


def get_lockout_ip() -> Lockout:
    """Cubo por IP (anti password-spraying). Clave prefijada: no colisiona con el cubo por email."""
    s = get_settings()
    return _RedisLockout(_cliente_redis(s.redis_url), _LOGIN_IP_MAX_INTENTOS, _LOGIN_IP_VENTANA_S)


@router.post("/auth/login/password", response_model=LoginOut)
async def login_password(
    datos: EmailLogin,
    request: Request,
    directorio: Directorio = Depends(get_directorio),
    lockout: Lockout = Depends(get_lockout),
    lockout_ip: Lockout = Depends(get_lockout_ip),
) -> LoginOut:
    """Autentica por email/contraseña y emite el JWT con el `tenant` de la empresa del usuario."""
    clave = datos.email.strip().lower()
    clave_ip = f"ip:{client_ip(request)}"

    if await lockout.bloqueado(clave) or await lockout_ip.bloqueado(clave_ip):
        log.warning("login_password_bloqueado", email_hash=hash(clave))   # nunca el email en claro
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Demasiados intentos. Intenta más tarde.")

    identidad = await directorio.buscar(datos.email)

    # Tiempo constante: SIEMPRE se verifica un hash (real o dummy), no se ramifica por la causa
    # (email inexistente igual paga el costo de argon2). El slug NO se resuelve aquí: solo se usa
    # para emitir el token en el éxito, así que pedirlo antes filtraría por timing si el email existe.
    hash_real = identidad.password_hash if (identidad and identidad.password_hash) else None
    coincide = verify_password(datos.password, hash_real or _DUMMY_HASH)
    autenticado = bool(coincide and identidad and identidad.activo and identidad.password_hash)

    if not autenticado:
        await lockout.registrar_fallo(clave)
        await lockout_ip.registrar_fallo(clave_ip)
        # Mensaje y status idénticos para email inexistente / clave errada / inactivo / sin clave.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    # PLATAFORMA (super-admin, ADR 0010 §D2): identidad sin empresa → JWT de plataforma SIN tenant. No
    # se resuelve ningún slug (empresa_id es NULL); opera cross-tenant sobre el control DB en /admin/*.
    if identidad.rol == "super_admin":
        await lockout.reset(clave)
        token = create_platform_token(user_id=identidad.usuario_id, rol=identidad.rol)
        log.info("login_password_ok_plataforma", usuario_id=identidad.usuario_id)
        return LoginOut(
            token=token, usuario=UsuarioOut(id=identidad.usuario_id, rol=identidad.rol, tenant=None)
        )

    # TENANT: el slug (query extra al control DB) se resuelve SOLO tras confirmar la credencial: ni fuga
    # de timing en email existente, ni query desperdiciada en los fallos. Si saliera None → fallo de auth.
    slug = await directorio.slug_empresa(identidad.empresa_id) if identidad.empresa_id else None
    if slug is None:
        await lockout.registrar_fallo(clave)
        await lockout_ip.registrar_fallo(clave_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")

    await lockout.reset(clave)
    token = create_access_token(user_id=identidad.usuario_id, tenant=slug, rol=identidad.rol)
    log.info("login_password_ok", tenant=slug, usuario_id=identidad.usuario_id, rol=identidad.rol)
    return LoginOut(
        token=token, usuario=UsuarioOut(id=identidad.usuario_id, rol=identidad.rol, tenant=slug)
    )
