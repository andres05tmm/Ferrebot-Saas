"""Set-password y reset por token de un solo uso (login real, ADR 0009 §D3, A1.3).

Las identidades se crean SIN contraseña (`password_hash` NULL); el usuario la establece por un enlace
con token. Tokens de UN SOLO USO con expiración, guardados en Redis bajo `sha256(token)` (NUNCA el token
en claro: la clave es el hash, el valor es el `identidad_id`, el TTL es la expiración). Consumir es
atómico (GETDEL) → reuso imposible. `set-password` y `reset/confirmar` son la MISMA operación
(token → nueva contraseña); `reset/solicitar` genera el token y lo loguea para entrega manual (el envío
de email real es un TODO aparte). SIN enumeración en `reset/solicitar`: 200 genérico exista o no el email.

Rutas eximidas del TenantMiddleware (`_AUTH_SIN_TENANT`): el flujo ocurre sobre el link compartido, sin
empresa resuelta. Token store y repo (control DB) se inyectan → testeable sin red.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
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
    store: TokenStore = Depends(get_token_store),
    repo: RepoIdentidades = Depends(get_repo_identidades),
) -> dict:
    """Solicita un reset. SIN enumeración: 200 genérico exista o no el email. Si existe, genera el token
    y lo LOGUEA para entrega manual (interim; el envío de email real es un TODO aparte)."""
    identidad = await repo.buscar_por_email(datos.email)
    if identidad is not None:
        token = await store.crear(identidad.id, get_settings().auth_token_ttl_segundos)
        # INTERIM: token al log para que el operador lo entregue a mano. TODO: enviar por email real.
        log.info("reset_token_generado", identidad_id=identidad.id, token=token)
    return {"detail": "Si el email existe, te enviaremos un enlace para restablecer la contraseña."}
