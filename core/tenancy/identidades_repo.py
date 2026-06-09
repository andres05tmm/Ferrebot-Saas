"""Repositorio del directorio de identidades en el control DB (login real, ADR 0009 §D1).

Único lugar que consulta `identidades`. Driver async (AsyncSession), como el resto de `core/tenancy`
(`control_repo`, `capacidades`): el login es un endpoint del API. El email se normaliza SIEMPRE a
minúsculas (la unicidad real la da el índice funcional `lower(email)`); el llamador maneja la
transacción (igual que el patrón de SQLAlchemy en este paquete) — los métodos de escritura no commitean.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_COLS = "id, email, password_hash, empresa_id, usuario_id, rol, activo"


@dataclass(frozen=True, slots=True)
class Identidad:
    """Una fila del directorio: el enlace `email → (empresa_id, usuario_id, rol)` para el login."""

    id: int
    email: str
    password_hash: str | None
    empresa_id: int | None   # None para una identidad de PLATAFORMA (super_admin, ADR 0010 §D2)
    usuario_id: int
    rol: str
    activo: bool


def _norm(email: str) -> str:
    """Normalización canónica del email (clave de búsqueda y almacenamiento): sin espacios, minúsculas."""
    return email.strip().lower()


def _fila(row) -> Identidad | None:
    if row is None:
        return None
    m = row._mapping
    return Identidad(
        id=m["id"], email=m["email"], password_hash=m["password_hash"],
        empresa_id=m["empresa_id"], usuario_id=m["usuario_id"], rol=m["rol"], activo=m["activo"],
    )


async def buscar_por_email(session: AsyncSession, email: str) -> Identidad | None:
    """Busca la identidad por email (case-insensitive). None si no existe."""
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM identidades WHERE lower(email) = :e"),
            {"e": _norm(email)},
        )
    ).first()
    return _fila(row)


async def upsert(
    session: AsyncSession, *, email: str, empresa_id: int, usuario_id: int, rol: str
) -> Identidad:
    """Crea o actualiza la identidad de un email (idempotente por email). NO toca `password_hash`.

    Re-ejecutar con el mismo email reapunta empresa/usuario/rol (no duplica). El commit es del llamador.
    """
    row = (
        await session.execute(
            text(
                f"INSERT INTO identidades (email, empresa_id, usuario_id, rol) "
                f"VALUES (:email, :empresa_id, :usuario_id, :rol) "
                f"ON CONFLICT (lower(email)) DO UPDATE SET "
                f"empresa_id = EXCLUDED.empresa_id, usuario_id = EXCLUDED.usuario_id, "
                f"rol = EXCLUDED.rol, actualizado_en = now() "
                f"RETURNING {_COLS}"
            ),
            {"email": _norm(email), "empresa_id": empresa_id, "usuario_id": usuario_id, "rol": rol},
        )
    ).first()
    return _fila(row)


async def set_password_hash(session: AsyncSession, identidad_id: int, password_hash: str) -> None:
    """Fija el `password_hash` de una identidad (set-password / reset). El commit es del llamador."""
    await session.execute(
        text("UPDATE identidades SET password_hash = :h, actualizado_en = now() WHERE id = :id"),
        {"h": password_hash, "id": identidad_id},
    )
