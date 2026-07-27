"""Config por empresa para bancos, leída del control DB (`config_empresa`, en claro).

Espeja `modules.caja.config`: SQL solo aquí (regla #2), sobre la sesión de control per-call.

`bancos_cuentas_alias` es un JSON `{"*3891": "Andrés", "*6485": "Farid"}`: le pone nombre a las
cuentas a las que entra la plata, para que el desglose diga a quién le llegó y no un número. Va en
`config_empresa` y no en una tabla propia porque son dos strings que cambian una vez en la vida
(mismo criterio que `pago_transferencia_titular`); se setea con `tools/set_config.py`.
"""
from __future__ import annotations

import json

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import control_session
from core.logging import get_logger

log = get_logger("bancos")


def parsear_alias(valor: str | None) -> dict[str, str]:
    """Texto de config → alias. Ausente, vacío, JSON inválido o no-objeto → `{}`.

    Default seguro: un alias mal escrito muestra el número de cuenta, nunca tumba el reporte de
    cuánta plata entró. Es una etiqueta, no un dato del negocio.
    """
    if not (valor or "").strip():
        return {}
    try:
        datos = json.loads(valor)
    except ValueError:
        log.warning("bancos_alias_invalido")
        return {}
    if not isinstance(datos, dict):
        log.warning("bancos_alias_no_es_objeto")
        return {}
    return {str(k): str(v) for k, v in datos.items() if v}


async def cargar_alias_cuentas(session: AsyncSession, empresa_id: int) -> dict[str, str]:
    """Alias de las cuentas bancarias de la empresa (`config_empresa.bancos_cuentas_alias`)."""
    valor = (
        await session.execute(
            text(
                "SELECT valor FROM config_empresa "
                "WHERE empresa_id = :e AND clave = 'bancos_cuentas_alias'"
            ),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    return parsear_alias(valor)


async def get_alias_cuentas(request: Request) -> dict[str, str]:
    """Alias de la empresa resuelta (control DB per-call; overridable en test)."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return {}
    async with control_session() as cs:
        return await cargar_alias_cuentas(cs, tenant.id)
