"""Motor del pack postventa (plan §2.6): determinista — barrido de eventos + dedup + respuestas.

El job (cron del worker) barre citas cumplidas / pedidos entregados que llevan `horas_tras_evento`
y aún no recibieron seguimiento, e invoca `enviar` (plantilla paga de Kapso) — solo un envío
exitoso sella el dedup (mismo seam que cobranza/reconfirmación). La calificación entra por la
herramienta del agente (`calificar_atencion`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Awaitable, Callable

from modules.postventa.models import EncuestaRespuesta
from modules.postventa.repository import SqlPostventaRepository

# Ventana máxima hacia atrás del barrido: eventos más viejos ya no se siguen (evita spamear
# históricos al prender el pack).
_VENTANA_MAX_HORAS = 48


@dataclass(frozen=True, slots=True)
class SeguimientoPendiente:
    """Un evento elegible para seguimiento (lo que ve el callback de envío)."""

    origen: str          # cita | pedido
    origen_id: int
    telefono: str
    nombre: str | None


@dataclass(frozen=True, slots=True)
class ResumenPostventa:
    """Resultado de una corrida del cron: cuántos seguimientos se enviaron."""

    enviados: int = 0


# Callback que envía el seguimiento (plantilla Kapso). True = éxito → se sella el dedup.
EnviarSeguimiento = Callable[[SeguimientoPendiente], Awaitable[bool]]


class PostventaService:
    def __init__(self, repo: SqlPostventaRepository) -> None:
        self._repo = repo

    async def procesar_seguimientos(
        self, *, ahora: datetime, enviar: EnviarSeguimiento
    ) -> ResumenPostventa:
        """Una corrida determinista: eventos en [ahora - 48h, ahora - horas_tras_evento) sin dedup."""
        config = await self._repo.obtener_config()
        if not config.activo:
            return ResumenPostventa()
        hasta = ahora - timedelta(hours=config.horas_tras_evento)
        desde = ahora - timedelta(hours=_VENTANA_MAX_HORAS)
        if hasta <= desde:
            return ResumenPostventa()

        pendientes: list[SeguimientoPendiente] = []
        if config.seguir_citas:
            pendientes += [
                SeguimientoPendiente(origen="cita", origen_id=f["id"],
                                     telefono=f["telefono"], nombre=f["nombre"])
                for f in await self._repo.citas_para_seguimiento(desde=desde, hasta=hasta)
            ]
        if config.seguir_pedidos:
            pendientes += [
                SeguimientoPendiente(origen="pedido", origen_id=f["id"],
                                     telefono=f["telefono"], nombre=f["nombre"])
                for f in await self._repo.pedidos_para_seguimiento(desde=desde, hasta=hasta)
            ]

        enviados = 0
        for p in pendientes:
            if not p.telefono:
                continue
            if await enviar(p):
                await self._repo.sellar_envio(
                    origen=p.origen, origen_id=p.origen_id, telefono=p.telefono
                )
                enviados += 1
        return ResumenPostventa(enviados=enviados)

    # --- cara al cliente (herramienta del agente) --------------------------------
    async def calificar(
        self, telefono: str, calificacion: int, *, comentario: str | None = None
    ) -> tuple[EncuestaRespuesta, str | None]:
        """Registra la calificación 1-5. Devuelve (respuesta, link_resena | None).

        El link de reseña (Google Maps) solo se ofrece si la calificación alcanza el umbral del
        negocio — a un cliente insatisfecho no se le pide reseña pública.
        """
        respuesta = await self._repo.registrar_respuesta(
            telefono=telefono, calificacion=calificacion, comentario=comentario
        )
        config = await self._repo.obtener_config()
        link = (
            config.google_maps_url
            if config.google_maps_url and calificacion >= config.calificacion_minima_resena
            else None
        )
        return respuesta, link

    # --- dashboard ----------------------------------------------------------------
    async def listar_respuestas(self):
        return await self._repo.listar_respuestas()

    async def satisfaccion(self) -> dict:
        return await self._repo.satisfaccion()

    async def obtener_config(self):
        return await self._repo.obtener_config()
