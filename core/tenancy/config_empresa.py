"""Lecturas de `config_empresa` (control DB, claves NO secretas) transversales al tenant.

SQL solo aquí (regla #2), sobre la sesión de control per-call — espeja `modules.ventas.config`.
La escritura la hacen `tools.set_config` y el provisionador (`cargar_secretos`).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def cargar_rubro(session: AsyncSession, empresa_id: int) -> str | None:
    """Rubro del negocio ("ferretería", "peluquería"…) para la persona del bot (`config_empresa.rubro`).

    Default seguro: NULL/ausente → None (el prompt cae al texto histórico de ferretería; los tenants
    existentes no cambian ni un byte hasta que se les setee el rubro con `tools.set_config`).
    """
    valor = (
        await session.execute(
            text("SELECT valor FROM config_empresa WHERE empresa_id = :e AND clave = 'rubro'"),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    valor = (valor or "").strip()
    return valor or None
