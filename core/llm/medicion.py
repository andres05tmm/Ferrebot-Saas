"""Medición de tokens por el borde del proveedor (token accounting del turno).

`ProveedorMedido` envuelve un `LLMProvider` y, en cada `generate`, lee `response.usage` y lo acumula
en un `CostosStore` inyectado. Va en el wrapper (no en `ai.agent`) porque el bucle del agente tiene
varios puntos de salida y un camino de 2 generaciones: contar en el borde del proveedor acumula las
dos llamadas de forma natural, sin tener que poblar el conteo en cada `return` del agente.

Best-effort (regla #6 de observabilidad): si el store lanza, se loguea y **NO** se rompe el `generate`
—el token accounting nunca debe degradar la respuesta al usuario. La normalización de las claves de
`usage` (Claude: `input_tokens`/`output_tokens`; OpenAI: `prompt_tokens`/`completion_tokens`) vive
aquí. La fecha es zona Colombia (regla #4). El `modelo` sale del `model` de cada `generate`.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Protocol

from core.config.timezone import today_co
from core.llm.base import LLMProvider, LLMResponse
from core.logging import get_logger

log = get_logger("core.llm.medicion")

# Métricas de agente (ADR 0024): el evento `llm_uso` lleva las dimensiones por llamada para derivar,
# desde el logging estructurado, tokens/conversación, latencia p95 y el ahorro del prompt caching
# (cache_read/creation). El nombre es estable a propósito: es la superficie que consume la observabilidad.
_EVENTO_METRICA = "llm_uso"


class CostosStore(Protocol):
    """Acumulador de tokens en `api_costo_diario` (PK=fecha; modelo = último escritor)."""

    async def acumular(
        self, *, fecha: date, modelo: str, tokens_in: int, tokens_out: int
    ) -> None: ...


class ProveedorMedido:
    """Decorador de `LLMProvider` que acumula los tokens de cada `generate` en un `CostosStore`.

    Además emite el evento de métrica `llm_uso` (latencia + tokens + caché) por llamada: es la
    infraestructura de observabilidad del agente (ADR 0024), best-effort como el resto de la medición.
    """

    def __init__(self, provider: LLMProvider, costos: CostosStore) -> None:
        self._provider = provider
        self._costos = costos
        self.nombre = provider.nombre
        self.api_key = provider.api_key

    async def generate(self, **kwargs: Any) -> LLMResponse:
        inicio = time.perf_counter()
        resp = await self._provider.generate(**kwargs)
        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        await self._contar(resp, kwargs.get("model"), latencia_ms)
        return resp

    async def _contar(self, resp: LLMResponse, modelo: str | None, latencia_ms: int) -> None:
        """Acumula los tokens y emite la métrica (best-effort: un fallo nunca degrada el `generate`)."""
        try:
            usage = resp.usage or {}
            tokens_in = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            tokens_out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            # Tokens de prompt caching (solo Claude los reporta; 0/ausente en el resto).
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
            # Métrica de agente: se emite SIEMPRE que haya latencia útil (aun sin usage: mide el turno).
            log.info(
                _EVENTO_METRICA, proveedor=self.nombre, modelo=modelo or "",
                tokens_in=tokens_in, tokens_out=tokens_out,
                cache_read=cache_read, cache_creation=cache_creation, latencia_ms=latencia_ms,
            )
            if not tokens_in and not tokens_out:
                return                          # sin usage no se escribe en el ledger de costo
            await self._costos.acumular(
                fecha=today_co(), modelo=modelo or "",
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        except Exception:
            log.warning("costos_acumular_fallo", modelo=modelo, exc_info=True)
