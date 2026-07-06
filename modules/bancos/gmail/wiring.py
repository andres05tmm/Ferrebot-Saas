"""Composition root del webhook Bancolombia: arma `WebhookGmailDeps` con adaptadores reales.

La lógica vive en `modules.bancos.gmail.webhook` (testeable con fakes). Aquí solo el cableado: resolver
token→empresa en el control DB y encolar `procesar_gmail_push` en ARQ.
"""
from __future__ import annotations

from typing import Any

from core.db.session import control_session
from modules.bancos.gmail.registro import RegistroGmail
from modules.bancos.gmail.webhook import WebhookGmailDeps

JOB_PROCESAR = "procesar_gmail_push"


def construir_webhook_bancolombia_deps(arq_pool: Any) -> WebhookGmailDeps:
    async def resolver(token: str) -> int | None:
        async with control_session() as cs:
            cuenta = await RegistroGmail(cs).resolver_por_token(token)
        return None if cuenta is None else cuenta.empresa_id

    async def encolar(empresa_id: int, history_id: str | None) -> None:
        await arq_pool.enqueue_job(JOB_PROCESAR, empresa_id, history_id)

    return WebhookGmailDeps(resolver=resolver, encolar=encolar)
