"""Errores de dominio del pack de conversación / handoff (el router los mapea a HTTP)."""


class ConversacionError(Exception):
    """Base de los errores del pack de conversación."""


class ConversacionInexistente(ConversacionError):
    def __init__(self, conversacion_id: int) -> None:
        super().__init__(f"La conversación {conversacion_id} no existe")
        self.conversacion_id = conversacion_id


class ConversacionNoEnHumano(ConversacionError):
    """Se intentó responder como asesor una conversación que no está en `humano` (tómala primero)."""

    def __init__(self, conversacion_id: int) -> None:
        super().__init__(
            f"La conversación {conversacion_id} no está en manos de un humano: tómala antes de responder"
        )
        self.conversacion_id = conversacion_id


class SinCanalWhatsapp(ConversacionError):
    """La empresa no tiene un número de WhatsApp activo: no puede enviar la respuesta saliente."""

    def __init__(self) -> None:
        super().__init__("La empresa no tiene un canal de WhatsApp activo para responder")
