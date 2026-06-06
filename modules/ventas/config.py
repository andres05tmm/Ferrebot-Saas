"""Config por empresa para ventas, leída del control DB (config_empresa, en claro).

Espeja `modules.facturacion.config.cargar_ambiente`: SQL solo aquí (regla #2), sobre la sesión de
control que recibe (per-call). El control de stock estricto es una capacidad OPT-IN por empresa; el
default es PERMISIVO (no bloquear la venta por falta de stock).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def cargar_control_stock_estricto(session: AsyncSession, empresa_id: int) -> bool:
    """¿La empresa exige control de stock estricto? (`config_empresa.control_stock_estricto == 'true'`).

    Default seguro PERMISIVO: ausente o valor desconocido → False (la venta nunca se bloquea por stock;
    el stock puede quedar negativo y se corrige al registrar la compra faltante).
    """
    valor = (
        await session.execute(
            text(
                "SELECT valor FROM config_empresa "
                "WHERE empresa_id = :e AND clave = 'control_stock_estricto'"
            ),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    return (valor or "").strip().lower() == "true"
