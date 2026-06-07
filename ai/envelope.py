"""Envelope común de las herramientas IA (ai-tools.md §3).

El modelo solo ve los `args` de cada herramienta; el resto del sobre lo inyecta el despachador
desde el contexto del turno. Aquí viven los tres tipos del contrato:

  - `Contexto`  → lo inyectado (tenant, usuario, rol, origen, idempotencia, capacidades…).
  - `Resultado` → éxito: payload + texto para el usuario + evento SSE + estado de idempotencia.
  - `ErrorTool` → fallo con código estable (tabla §3) y si el modelo puede repreguntar.

`Contexto.capacidades` es el set de features EFECTIVAS de la empresa, ya resuelto y cacheado en
el contexto del tenant (feature-flags.md §almacenamiento, tenancy.md §3). El despachador lo lee
para exponer/ocultar herramientas; no re-consulta el control DB por herramienta.
"""
from dataclasses import dataclass, field
from typing import Any, Literal

# Códigos de error estables (ai-tools.md §3). El modelo decide repreguntar según `recuperable`.
EstadoIdempotente = Literal["aplicada", "duplicada"] | None


@dataclass(frozen=True, slots=True)
class Contexto:
    """Contexto inyectado por el despachador (nunca lo provee el modelo)."""

    tenant_id: int
    usuario_id: int
    rol: str                              # vendedor | admin | super_admin (core.auth.rbac)
    origen: str = "bot"                   # web | bot | voz | offline
    idempotency_key: str | None = None    # generado por el cliente/bot
    request_id: str | None = None
    capacidades: frozenset[str] = frozenset()  # features efectivas de la empresa
    confirmado: bool = False              # True si el usuario ya confirmó (riel de confirmación)
    # Identidad del cliente en canales de cara al público (WhatsApp): la inyecta el adaptador de
    # canal desde el número que escribe, NUNCA el modelo. Las herramientas de agenda acotan sus
    # acciones a ESTE teléfono; el modelo no puede pasar otro ni ver citas ajenas.
    cliente_telefono: str | None = None

    def tiene_capacidad(self, feature: str | None) -> bool:
        """Núcleo (`feature is None`) siempre disponible; lo demás según el set efectivo."""
        return feature is None or feature in self.capacidades


@dataclass(frozen=True, slots=True)
class Resultado:
    """Salida exitosa de una herramienta (lo que vuelve al modelo y al canal)."""

    data: dict[str, Any]
    resumen: str                          # texto en lenguaje natural para el usuario
    evento: str | None = None             # evento SSE emitido por el servicio (o None)
    idempotente: EstadoIdempotente = None
    ok: Literal[True] = True


@dataclass(frozen=True, slots=True)
class ErrorTool:
    """Fallo de una herramienta con código estable (ai-tools.md §3)."""

    error: str                            # producto_no_encontrado | stock_insuficiente | …
    detail: str = ""
    recuperable: bool = False             # True = el modelo puede repreguntar/ajustar
    ok: Literal[False] = False
