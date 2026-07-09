"""Email transaccional de plataforma (reset de contraseña, ADR 0009 §D3).

Un solo puerto (`EmailSender`) con dos implementaciones: Brevo (API v3) y un fallback que solo
loguea cuando no hay proveedor configurado. Ver `core.email.sender`.
"""
from core.email.sender import (
    BrevoSender,
    EmailSender,
    LogSender,
    construir_payload,
    construir_sender,
)

__all__ = ["BrevoSender", "EmailSender", "LogSender", "construir_payload", "construir_sender"]
