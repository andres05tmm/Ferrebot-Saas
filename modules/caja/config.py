"""Config por empresa para caja, leída del control DB (config_empresa, en claro).

Espeja `modules.ventas.config`: SQL solo aquí (regla #2), sobre la sesión de control per-call.
`caja_obligatoria` es el modo "un cajón por empresa" (negocio familiar): OPT-IN; con el toggle ON
no se registra una venta sin una caja abierta EN LA EMPRESA (sin importar quién la abrió) y las
operaciones de caja actúan sobre ESA caja compartida. Default OFF: nada cambia para los demás
tenants (semántica por-usuario existente).
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import control_session


async def cargar_caja_obligatoria(session: AsyncSession, empresa_id: int) -> bool:
    """¿La empresa exige caja abierta para vender? (`config_empresa.caja_obligatoria == 'true'`).

    Default seguro OFF: ausente o valor desconocido → False (la venta nunca se bloquea por caja).
    """
    valor = (
        await session.execute(
            text(
                "SELECT valor FROM config_empresa "
                "WHERE empresa_id = :e AND clave = 'caja_obligatoria'"
            ),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    return (valor or "").strip().lower() == "true"


async def get_caja_obligatoria(request: Request) -> bool:
    """Toggle `caja_obligatoria` de la empresa resuelta (control DB per-call; overridable en test).

    Patrón de `get_control_stock_estricto`: sin empresa resuelta (apps mínimas de test) → False.
    """
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return False
    async with control_session() as cs:
        return await cargar_caja_obligatoria(cs, tenant.id)
