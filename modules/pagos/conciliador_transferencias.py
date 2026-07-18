"""Puente transferencia entrante → cobro de pedido → pedido pagado (plan demo Sirius §4, ADR 0028).

Cuando la ingesta Bancolombia inserta una transferencia entrante NUEVA, este módulo busca el cobro
del pedido que la explica (por monto exacto, dentro de una ventana), y SOLO si hay EXACTAMENTE UN
candidato lo marca `pagado` y dispara la cascada del contrato: SSE `pedido_pagado` + notificación al
cliente + aviso al negocio. Con 0 o ≥2 candidatos NO toca nada (queda para el cierre manual en
TabCobros, `marcar_pagado_manual`, que dispara la MISMA cascada — por eso vive factorizada aquí).

Los transportes de notificación se INYECTAN como callbacks (`notificar_cliente`/`notificar_negocio`):
este módulo no importa Telegram/Kapso; el worker/script arma los callbacks con la credencial del
tenant. La sesión es la del tenant (la frontera de aislamiento).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events.publisher import publish as _publish_real
from core.logging import get_logger
from modules.pagos.models import Cobro
from modules.pagos.repository import SqlPagosRepository

log = get_logger("pagos.conciliador_transferencias")

# Ventana para casar una transferencia con el cobro de un pedido: un almuerzo se paga poco después de
# confirmarlo. 6h cubre el turno de servicio sin arrastrar cobros viejos de montos que se repiten.
VENTANA = timedelta(hours=6)

# Transportes inyectados (el módulo NO conoce Telegram/Kapso: los arma el worker/script por tenant).
NotificarCliente = Callable[[str, str], Awaitable[None]]   # (cliente_telefono, texto)
NotificarNegocio = Callable[[str], Awaitable[None]]         # (texto)
Publicar = Callable[[AsyncSession, str, dict], Awaitable[None]]


async def cascada_pedido_pagado(
    session: AsyncSession,
    cobro: Cobro,
    *,
    notificar_cliente: NotificarCliente | None = None,
    notificar_negocio: NotificarNegocio | None = None,
    publicar: Publicar = _publish_real,
) -> None:
    """Efectos de que un cobro de PEDIDO quede pagado: SSE `pedido_pagado` (contrato) + avisos.

    Compartida por el conciliador automático y por `marcar_pagado_manual` (dashboard) para no
    duplicar el efecto. No-op si el cobro no es de un pedido. Las notificaciones son best-effort:
    un fallo de transporte NO revierte el pago ni tumba la cascada."""
    if cobro.origen != "pedido" or cobro.origen_id is None:
        return
    n = cobro.origen_id
    await publicar(session, "pedido_pagado", {
        "pedido_id": n, "cobro_id": cobro.id, "monto": str(cobro.monto),
    })
    if notificar_cliente is not None and cobro.cliente_telefono:
        try:
            await notificar_cliente(
                cobro.cliente_telefono,
                f"¡Pago recibido! 🎉 Tu pedido #{n} entró a cocina.",
            )
        except Exception:  # noqa: BLE001 — el aviso no es transaccional con el pago
            log.warning("conciliador_notificar_cliente_fallo", cobro_id=cobro.id, exc_info=True)
    if notificar_negocio is not None:
        try:
            await notificar_negocio(f"💵 Pago confirmado del pedido #{n} (${cobro.monto}).")
        except Exception:  # noqa: BLE001
            log.warning("conciliador_notificar_negocio_fallo", cobro_id=cobro.id, exc_info=True)


async def _desempatar_por_comprobante(
    repo: SqlPagosRepository, candidatos: list[Cobro], monto: Decimal
) -> Cobro | None:
    """Entre ≥2 candidatos por monto, gana el ÚNICO con comprobante asociado; si no, None (no-op).

    El comprobante (foto que mandó el cliente) rompe el empate: 0 o >1 con comprobante sigue siendo
    ambiguo y queda para el cierre manual."""
    con_comprobante = await repo.cobro_ids_con_comprobante([c.id for c in candidatos])
    conflictantes = [c for c in candidatos if c.id in con_comprobante]
    if len(conflictantes) == 1:
        cobro = conflictantes[0]
        log.info("conciliador_transferencia_desempate_comprobante",
                 candidatos=len(candidatos), cobro_id=cobro.id, monto=str(monto))
        return cobro
    log.info("conciliador_transferencia_sin_match", candidatos=len(candidatos),
             con_comprobante=len(conflictantes), monto=str(monto))
    return None


async def conciliar_transferencia(
    session: AsyncSession,
    *,
    monto: Decimal,
    notificar_cliente: NotificarCliente | None = None,
    notificar_negocio: NotificarNegocio | None = None,
    publicar: Publicar = _publish_real,
) -> Cobro | None:
    """Casa UNA transferencia entrante (por `monto`) contra los cobros de pedido pendientes.

    REGLA DURA (ADR 0028): con EXACTAMENTE UN candidato en la ventana se marca `pagado` y se
    dispara la cascada. Con ≥2 candidatos por monto, DESEMPATE por comprobante: si EXACTAMENTE UNO
    de ellos tiene un comprobante asociado (foto que mandó el cliente), ese gana. Con 0 candidatos,
    o ≥2 sin desempate (0 o >1 con comprobante), devuelve None sin tocar nada (→ cierre manual).
    Devuelve el cobro marcado, o None."""
    repo = SqlPagosRepository(session)
    desde = now_co() - VENTANA
    candidatos = await repo.cobros_pedido_pendientes_por_monto(monto, desde=desde)

    if len(candidatos) == 1:
        cobro = candidatos[0]
    elif len(candidatos) >= 2:
        cobro = await _desempatar_por_comprobante(repo, candidatos, monto)
        if cobro is None:
            return None
    else:
        log.info("conciliador_transferencia_sin_match", candidatos=0, monto=str(monto))
        return None

    await repo.marcar(cobro, "pagado")
    await cascada_pedido_pagado(
        session, cobro,
        notificar_cliente=notificar_cliente, notificar_negocio=notificar_negocio, publicar=publicar,
    )
    log.info("conciliador_transferencia_pagado", cobro_id=cobro.id, pedido_id=cobro.origen_id,
             monto=str(monto))
    return cobro
