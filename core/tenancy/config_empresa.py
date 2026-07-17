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


async def cargar_datos_pago(session: AsyncSession, empresa_id: int) -> tuple[str | None, str | None]:
    """Datos de la cuenta de transferencia del negocio → `(titular, numero)` (config_empresa).

    Claves `pago_transferencia_titular` / `pago_transferencia_numero` (NO secretas: es lo que el
    negocio le dicta al cliente para que transfiera). Ausentes → None; el agente informa el total sin
    número de cuenta (degradación segura, nunca inventa datos de pago).
    """
    filas = (
        await session.execute(
            text(
                "SELECT clave, valor FROM config_empresa WHERE empresa_id = :e "
                "AND clave IN ('pago_transferencia_titular', 'pago_transferencia_numero')"
            ),
            {"e": empresa_id},
        )
    ).all()
    valores = {clave: (valor or "").strip() or None for clave, valor in filas}
    return valores.get("pago_transferencia_titular"), valores.get("pago_transferencia_numero")


async def cargar_auto_facturar_venta(session: AsyncSession, empresa_id: int) -> bool:
    """¿La venta auto-emite documento fiscal (POS/FE) al registrarse? (`config_empresa.facturar_en_venta`).

    Default TRUE = comportamiento histórico (los tenants existentes no cambian sin setear la clave).
    `facturar_en_venta='false'` (o 0/no/off) → la venta queda INTERNA y se factura A PEDIDO
    (POST /facturas). Solo cambia el DEFAULT por venta: una intención fiscal EXPLÍCITA se respeta igual.
    """
    valor = (
        await session.execute(
            text("SELECT valor FROM config_empresa WHERE empresa_id = :e AND clave = 'facturar_en_venta'"),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    if valor is None:
        return True
    return str(valor).strip().lower() not in ("false", "0", "no", "off")
