"""Motor del pack pagar (ADR 0019): determinista, igual para todos los tenants.

Lee las cuentas por pagar (`facturas_proveedores`, solo lectura) y decide, sin IA, cuáles ameritan un
aviso al DUEÑO: las que vencen pronto (≤ `dias_aviso_previo`) o ya vencieron. El aviso es UN resumen
interno para el dueño (no un mensaje de cara a un proveedor): por eso no hay opt-out ni promesas.

El envío real del aviso (Fase 2: worker + canal del dueño) se inyecta como callback `enviar` — mismo
seam que `pack_cobranza`: solo un envío exitoso sella el dedup, así un fallo de red se reintenta en la
próxima corrida sin perder ni duplicar el aviso.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from core.logging import get_logger
from modules.pagar.repository import FacturaPendiente, SqlPagarRepository
from modules.pagar.schemas import PagarConfigActualizar

log = get_logger("pagar")


@dataclass(frozen=True, slots=True)
class CuentaPorPagar:
    """Una cuenta por pagar clasificada: su vencimiento efectivo y si amerita aviso."""

    factura_id: str
    proveedor: str
    pendiente: Decimal
    fecha: date
    vencimiento_efectivo: date
    dias_para_vencer: int   # negativo = ya vencida
    por_vencer: bool        # vence dentro de la ventana de aviso previo (aún no vencida)
    vencida: bool


@dataclass(frozen=True, slots=True)
class AvisoPagar:
    """El resumen que recibe el callback de envío: lo que el dueño necesita saber HOY."""

    cuentas: tuple[CuentaPorPagar, ...]
    total_por_vencer: Decimal
    total_vencido: Decimal
    generado_en: datetime


@dataclass(frozen=True, slots=True)
class ResumenPagar:
    """Resultado de una corrida del cron: si se envió aviso y cuántas facturas incluyó."""

    avisos_enviados: int = 0
    facturas_notificadas: int = 0


# Callback que entrega el aviso al dueño (lo provee el worker en Fase 2). Devuelve True si el envío
# fue exitoso (solo entonces se sella el dedup). Inyectable: los tests lo falsean.
EnviarAviso = Callable[[AvisoPagar], Awaitable[bool]]


def clasificar_cuenta(
    factura: FacturaPendiente, *, hoy: date, dias_aviso_previo: int, plazo_default_dias: int
) -> CuentaPorPagar:
    """Función pura: deriva el vencimiento efectivo y marca `por_vencer` / `vencida`.

    Vencimiento efectivo = `fecha_vencimiento`, o `fecha + plazo_default_dias` si es NULL.
    `por_vencer` y `vencida` son mutuamente excluyentes (una factura vencida no está "por vencer").
    """
    vencimiento = factura.fecha_vencimiento or (factura.fecha + timedelta(days=plazo_default_dias))
    dias = (vencimiento - hoy).days
    vencida = dias < 0
    por_vencer = 0 <= dias <= dias_aviso_previo
    return CuentaPorPagar(
        factura_id=factura.factura_id,
        proveedor=factura.proveedor,
        pendiente=factura.pendiente,
        fecha=factura.fecha,
        vencimiento_efectivo=vencimiento,
        dias_para_vencer=dias,
        por_vencer=por_vencer,
        vencida=vencida,
    )


class PagarService:
    def __init__(self, repo: SqlPagarRepository) -> None:
        self._repo = repo

    # --- lectura (motor + dashboard de Fase 2) --------------------------------
    async def cuentas_por_pagar(self, hoy: date) -> list[CuentaPorPagar]:
        """Todas las cuentas con saldo, clasificadas (por vencer / vencidas) según la config."""
        config = await self._repo.obtener_config()
        return [
            clasificar_cuenta(
                f, hoy=hoy,
                dias_aviso_previo=config.dias_aviso_previo,
                plazo_default_dias=config.plazo_default_dias,
            )
            for f in await self._repo.facturas_pendientes()
        ]

    # --- corrida del cron (worker, Fase 2) ------------------------------------
    async def procesar_avisos(self, *, ahora: datetime, enviar: EnviarAviso) -> ResumenPagar:
        """Una corrida determinista sobre la base del tenant. `ahora` se inyecta (hora Colombia).

        1) Si la config está inactiva → no se hace nada.
        2) Ventana horaria: fuera de `[hora_inicio, hora_fin)` NO se envía nada.
        3) Por factura con saldo: amerita aviso si está `por_vencer` o `vencida`; la cadencia
           (`cadencia_dias` desde el último aviso de ESA factura) evita repetir.
        4) Se arma UN resumen para el dueño con las facturas elegibles; solo un `enviar` exitoso sella
           el dedup de TODAS ellas (fallo de red → se reintenta en la próxima corrida).
        """
        config = await self._repo.obtener_config()
        if not config.activo:
            return ResumenPagar()
        if not (config.hora_inicio <= ahora.time() < config.hora_fin):
            return ResumenPagar()

        hoy = ahora.date()
        cadencia = timedelta(days=config.cadencia_dias)
        elegibles: list[CuentaPorPagar] = []
        for factura in await self._repo.facturas_pendientes():
            cuenta = clasificar_cuenta(
                factura, hoy=hoy,
                dias_aviso_previo=config.dias_aviso_previo,
                plazo_default_dias=config.plazo_default_dias,
            )
            if not (cuenta.por_vencer or cuenta.vencida):
                continue                          # vence más allá de la ventana: aún no amerita aviso
            if (
                factura.ultimo_aviso_en is not None
                and ahora - factura.ultimo_aviso_en < cadencia
            ):
                continue                          # cadencia: ya se avisó de esta factura hace poco
            elegibles.append(cuenta)

        if not elegibles:
            return ResumenPagar()

        aviso = AvisoPagar(
            cuentas=tuple(elegibles),
            total_por_vencer=sum((c.pendiente for c in elegibles if c.por_vencer), Decimal("0")),
            total_vencido=sum((c.pendiente for c in elegibles if c.vencida), Decimal("0")),
            generado_en=ahora,
        )
        if not await enviar(aviso):
            return ResumenPagar()                 # envío fallido: no se sella (se reintenta luego)

        await self._repo.sellar_avisos([c.factura_id for c in elegibles], cuando=ahora)
        log.info("pagar_avisos_enviados", facturas=len(elegibles))
        return ResumenPagar(avisos_enviados=1, facturas_notificadas=len(elegibles))

    # --- config (dashboard de Fase 2) -----------------------------------------
    async def obtener_config(self):
        return await self._repo.obtener_config()

    async def guardar_config(self, datos: PagarConfigActualizar):
        return await self._repo.guardar_config(datos)
