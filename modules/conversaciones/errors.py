"""Errores de dominio del pack de conversación / handoff (el router los mapea a HTTP)."""


class ConversacionError(Exception):
    """Base de los errores del pack de conversación."""


class ConversacionInexistente(ConversacionError):
    def __init__(self, conversacion_id: int) -> None:
        super().__init__(f"La conversación {conversacion_id} no existe")
        self.conversacion_id = conversacion_id
