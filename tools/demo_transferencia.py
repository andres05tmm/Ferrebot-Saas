"""Plan B de la demo Sirius: inyecta una transferencia entrante y dispara TODA la cascada real.

    python -m tools.demo_transferencia <slug> <monto>

Simula SOLO el correo de Bancolombia: construye un email con el formato real (mismo parser que la
ingesta de producción), lo mete por el MISMO camino (`procesar_push`), y engancha el conciliador de
transferencias. Si hay EXACTAMENTE UN cobro de pedido pendiente por ese monto (ventana 6h), el pago se
detecta solo → SSE `pedido_pagado` al kanban en vivo + notificación real al cliente por Telegram.

Es idempotente: el `gmail_message_id` es determinístico por (slug, monto, día), así que correrlo dos
veces con el mismo monto el mismo día NO duplica ni re-notifica (para volver a disparar, usa otro monto).

Notificación al cliente: si su teléfono empieza con `tg:` (canal Telegram público), se envía por la Bot
API con el token del tenant (`tg_publico_bot_token`, cifrado en secretos_empresa). Si el secreto aún no
existe, degrada con un warning (no rompe): la cascada y el SSE ocurren igual.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from decimal import Decimal, InvalidOperation

from apps.bot.telegram import TelegramNotificador
from apps.tg_publico.repos import SecretosTgPublico
from core.config import get_settings
from core.config.timezone import now_co, today_co
from core.db.session import control_session, tenant_session
from core.logging import configure_logging, get_logger
from core.tenancy.control_repo import resolve_tenant_by_slug
from modules.bancos.gmail.ingesta import procesar_push
from modules.bancos.repository import SqlBancosRepository
from modules.pagos.conciliador_transferencias import conciliar_transferencia

log = get_logger("demo_transferencia")


def _monto_fmt(monto: int) -> str:
    """Entero de pesos → notación colombiana con puntos de miles ('25000' → '25.000')."""
    return f"{monto:,}".replace(",", ".")


def _email_demo(monto: int) -> str:
    """Cuerpo de un correo de pago entrante de Bancolombia (formato real del parser, con el monto dado)."""
    hoy = today_co().strftime("%d/%m/%Y")
    hora = now_co().strftime("%H:%M")
    return (
        f"Bancolombia: Sirius, recibiste un pago de CLIENTE DEMO SIRIUS por ${_monto_fmt(monto)} "
        f"en tu cuenta *3891 conectado a la llave 0046052593 el {hoy} a las {hora}. "
        "Con codigo QR es facil y de una."
    )


class _FakeGmailCliente:
    """GmailCliente falso: sirve UN correo Bancolombia fijo (mismo shape que el cliente real de la ingesta)."""

    def __init__(self, message_id: str, body: str) -> None:
        self._mid = message_id
        self._body = body
        self.refresh_token_rotado = None

    async def ids_desde_history(self, history_id: str) -> list[str]:
        return [self._mid]

    async def headers(self, message_id: str) -> list[dict]:
        return [
            {"name": "From", "value": "notificaciones@bancolombia.com.co"},
            {"name": "Subject", "value": "Recibiste una transferencia"},
        ]

    async def mensaje_completo(self, message_id: str) -> dict:
        data = base64.urlsafe_b64encode(self._body.encode()).decode().rstrip("=")
        return {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": data}}]}}


async def inyectar(slug: str, monto: int, ref: str = "") -> int:
    """Inyecta la transferencia y corre el conciliador. Devuelve el número de filas nuevas insertadas."""
    settings = get_settings()
    async with control_session() as cs:
        tenant = await resolve_tenant_by_slug(cs, slug)
        if tenant is None:
            raise ValueError(f"empresa '{slug}' no existe en el control DB")
        bot_token = await SecretosTgPublico(cs, settings.secrets_master_key).bot_token(tenant.id)

    if not bot_token:
        log.warning("demo_sin_tg_publico_bot_token", slug=slug,
                    detalle="notificación al cliente se omitirá; corre tools.set_tg_publico primero")

    monto_dec = Decimal(monto)
    body = _email_demo(monto)
    # Determinístico por (slug, monto, día[, ref]) → idempotente: reprocesar el mismo correo no
    # duplica. `--ref` permite un SEGUNDO pago del mismo monto en el día (dos pedidos iguales).
    mid = f"demo-{slug}-{monto}-{today_co().isoformat()}" + (f"-{ref}" if ref else "")
    cliente = _FakeGmailCliente(mid, body)

    async def notificar(texto: str) -> None:
        """Aviso al negocio de 'transferencia recibida' (lo hace la ingesta real vía Telegram); aquí a log."""
        log.info("demo_transferencia_recibida", slug=slug, monto=str(monto_dec))

    async def notificar_cliente(cliente_telefono: str, texto: str) -> None:
        """Notifica al cliente por su canal. Solo Telegram público (`tg:`) en el script de demo."""
        if not cliente_telefono.startswith("tg:"):
            log.warning("demo_canal_cliente_no_soportado", telefono=cliente_telefono)
            return
        if not bot_token:
            log.warning("demo_notificacion_cliente_omitida", telefono=cliente_telefono,
                        detalle="sin tg_publico_bot_token")
            return
        chat_id = int(cliente_telefono[len("tg:"):])
        await TelegramNotificador(bot_token=bot_token).responder(chat_id, texto)
        log.info("demo_cliente_notificado", chat_id=chat_id)

    async def notificar_negocio(texto: str) -> None:
        log.info("demo_negocio_avisado", texto=texto)
        print(f"  → negocio: {texto}")

    insertados = 0
    async for s in tenant_session(tenant):
        async def al_insertar(mov, _s=s) -> None:
            cobro = await conciliar_transferencia(
                _s, monto=mov.monto,
                notificar_cliente=notificar_cliente, notificar_negocio=notificar_negocio,
            )
            if cobro is None:
                print("  · sin match único: 0 o ≥2 cobros de pedido pendientes por ese monto "
                      "(queda para cierre manual en TabCobros)")
            else:
                print(f"  ✓ pedido #{cobro.origen_id} marcado PAGADO (cobro #{cobro.id})")

        resultado = await procesar_push(
            cliente=cliente, repo=SqlBancosRepository(s), last_history_id="1",
            notificar=notificar, al_insertar=al_insertar, history_id_push="2",
        )
        insertados = resultado.insertados
    return insertados


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Inyecta una transferencia entrante de demo (plan B).")
    parser.add_argument("slug", help="slug de la empresa (p. ej. sirius)")
    parser.add_argument("monto", help="monto EXACTO transferido, en pesos (el total del pedido)")
    parser.add_argument("--ref", default="", help="sufijo para repetir el mismo monto en el día (p. ej. --ref 2)")
    args = parser.parse_args(argv)

    try:
        monto = int(Decimal(args.monto))
    except (InvalidOperation, ValueError):
        print(f"error: monto inválido: {args.monto!r}", file=sys.stderr)
        return 1
    if monto <= 0:
        print("error: el monto debe ser positivo", file=sys.stderr)
        return 1

    try:
        insertados = asyncio.run(inyectar(args.slug, monto, args.ref))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if insertados == 0:
        print(f"↺ transferencia de ${_monto_fmt(monto)} ya estaba inyectada hoy (idempotente): nada que hacer")
    else:
        print(f"✓ transferencia de ${_monto_fmt(monto)} inyectada en '{args.slug}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
