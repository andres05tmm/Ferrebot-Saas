"""Observabilidad de errores con Sentry, compartida por los 3 servicios (api/bot/worker).

No-op cuando no hay DSN configurado (dev y tests intactos): `init_sentry` solo llama a
`sentry_sdk.init` si `settings.sentry_dsn` trae valor. Las integraciones de FastAPI y ARQ las
auto-activa sentry-sdk al detectar los paquetes instalados; no se fuerzan manualmente.
"""
from __future__ import annotations

import sentry_sdk

from core.config import Settings, get_settings
from core.logging import get_logger

log = get_logger("observability")


def init_sentry(service: str, *, settings: Settings | None = None) -> bool:
    """Inicializa Sentry para `service` (api|bot|worker). No-op si no hay DSN.

    Devuelve True si inicializó, False si se omitió (sin DSN). `settings` es inyectable para tests;
    si no se pasa, se leen los settings de plataforma cacheados.
    """
    settings = settings or get_settings()
    if not settings.sentry_dsn:
        log.info("sentry_omitido", service=service)
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service)
    log.info("sentry_iniciado", service=service, environment=settings.sentry_environment)
    return True
