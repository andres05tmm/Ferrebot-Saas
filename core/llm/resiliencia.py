"""Resiliencia de la capa LLM (ADR 0023): retry con backoff+jitter y fallback de proveedor.

`ProveedorResiliente` decora un `LLMProvider` (mismo patrón que `ProveedorMedido`): los proveedores
siguen "sin lógica" y el bucle del agente no cambia — el reintento vive en el borde del `generate`,
por lo que JAMÁS re-ejecuta herramientas ya despachadas (solo se repite la llamada al modelo).

Política:
  - Reintenta SOLO `LLMTransitorio` (429/5xx/timeout/conexión), con backoff exponencial y jitter.
  - `LLMPermanente` (4xx de petición/auth) y excepciones desconocidas se propagan de inmediato.
  - Con `respaldo`: al agotar los reintentos del primario cae UNA vez al otro proveedor (con su
    propio modelo), sin reintentos anidados. Un permanente no activa el respaldo (fallaría igual).

`clasificar_excepcion` traduce errores crudos de SDK a las excepciones canónicas por duck-typing
(`status_code` / nombre de la clase), para no importar los SDKs en esta capa.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from core.llm.base import LLMPermanente, LLMProvider, LLMResponse, LLMTransitorio
from core.logging import get_logger

log = get_logger("core.llm.resiliencia")

_INTENTOS = 3
_BASE_S = 0.5
_TOPE_S = 8.0


def clasificar_excepcion(exc: Exception) -> Exception:
    """Error crudo de un SDK → excepción canónica (o el mismo error si no es clasificable).

    Duck-typing sobre `status_code` y el nombre de la clase: 429/5xx/timeout/conexión son
    transitorios; el resto de códigos HTTP son permanentes. Lo desconocido se devuelve intacto
    (el retry NO lo toca: ante la duda, no reintentar).
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status == 429 or status >= 500:
            return LLMTransitorio(f"{type(exc).__name__}: {exc}")
        return LLMPermanente(f"{type(exc).__name__}: {exc}")
    nombre = type(exc).__name__.lower()
    if "timeout" in nombre or "connection" in nombre:
        return LLMTransitorio(f"{type(exc).__name__}: {exc}")
    return exc


class ProveedorResiliente:
    """Decorador de `LLMProvider`: reintentos ante transitorios + fallback opcional de proveedor."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        respaldo: LLMProvider | None = None,
        modelo_respaldo: str | None = None,
        intentos: int = _INTENTOS,
        base_s: float = _BASE_S,
        tope_s: float = _TOPE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._provider = provider
        self._respaldo = respaldo
        self._modelo_respaldo = modelo_respaldo
        self._intentos = max(1, intentos)
        self._base_s = base_s
        self._tope_s = tope_s
        self._sleep = sleep
        self._rng = rng
        self.nombre = provider.nombre
        self.api_key = provider.api_key

    async def generate(self, **kwargs: Any) -> LLMResponse:
        try:
            return await self._con_reintentos(**kwargs)
        except LLMTransitorio:
            if self._respaldo is None:
                raise
            log.warning(
                "llm_fallback_proveedor", primario=self._provider.nombre,
                respaldo=self._respaldo.nombre,
            )
            kw = dict(kwargs)
            if self._modelo_respaldo:
                kw["model"] = self._modelo_respaldo
            return await self._respaldo.generate(**kw)

    async def _con_reintentos(self, **kwargs: Any) -> LLMResponse:
        for intento in range(1, self._intentos + 1):
            try:
                return await self._provider.generate(**kwargs)
            except LLMTransitorio as exc:
                if intento == self._intentos:
                    raise
                # Backoff exponencial con jitter: espera ∈ [0.5, 1.5) × base × 2^(intento−1).
                espera = min(self._base_s * 2 ** (intento - 1), self._tope_s) * (0.5 + self._rng())
                log.warning(
                    "llm_reintento", proveedor=self._provider.nombre, intento=intento,
                    espera_s=round(espera, 2), motivo=str(exc),
                )
                await self._sleep(espera)
        raise AssertionError("inalcanzable")  # pragma: no cover
