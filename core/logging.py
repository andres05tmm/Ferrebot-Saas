"""Logging estructurado (regla no negociable #6): nunca print.

Cada log lleva tenant_id y request_id desde contextvars que el middleware liga por request.
"""
import logging
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[int | None] = ContextVar("tenant_id", default=None)


def _inject_context(_logger, _method, event_dict: dict) -> dict:
    """Procesador que agrega request_id y tenant_id a cada evento."""
    rid = request_id_var.get()
    tid = tenant_id_var.get()
    if rid is not None:
        event_dict["request_id"] = rid
    if tid is not None:
        event_dict["tenant_id"] = tid
    return event_dict


def configure_logging() -> None:
    """Configura structlog (JSON). Idempotente."""
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "ferrebot"):
    return structlog.get_logger(name)
