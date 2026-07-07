"""Servicio del cotizador AIU: reglas de dominio sobre el repositorio (sin SQL).

Tres invariantes de negocio viven aquí:

  1. **Totales SIEMPRE por la función pura.** El desglose AIU se calcula con
     `services.calculations.aiu.calcular_totales_cotizacion` (IVA sólo sobre la utilidad); nunca se
     recalcula inline en router/Excel/service (una sola fuente de verdad, money-safe).
  2. **Ciclo de vida de estados EXPLÍCITO.** Las transiciones válidas están en `_TRANSICIONES`; un
     salto no contemplado → `TransicionEstadoInvalida` (409). Editar el builder sólo se permite
     mientras la cotización está viva (BORRADOR/ENVIADA); si no → `CotizacionNoEditable` (409).
  3. **Conversión GANADA→Obra idempotente.** Sólo una cotización GANADA se convierte; la creación de
     la `Obra` (1-1 con la cotización) la hace el módulo dueño `modules.obra` por un método aditivo
     idempotente (no duplica obra si ya se convirtió).

Depende de dos puertos: `CotizacionObraRepo` (su repositorio) y `ObrasConversion` (lo satisface
`modules.obra.service.ObrasService` con su método aditivo `crear_desde_cotizacion`); los tests los
falsean.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.config.timezone import today_co
from modules.cotizacion_obra.errors import (
    CotizacionInexistente,
    CotizacionNoEditable,
    CotizacionNoGanada,
    TransicionEstadoInvalida,
)
from modules.cotizacion_obra.schemas import (
    CotizacionObraActualizar,
    CotizacionObraCrear,
)
from modules.obra.models import CotizacionObra, ItemCotizacionObra, Obra
from services.calculations.aiu import TotalesAIU, calcular_totales_cotizacion

# Transiciones permitidas del ciclo de vida de una cotización (destinos válidos por estado actual).
# BORRADOR se envía o se descarta; una ENVIADA se gana/pierde/vence; una VENCIDA se puede reenviar
# (renovar vigencia). GANADA y PERDIDA son terminales.
_TRANSICIONES: dict[str, frozenset[str]] = {
    "BORRADOR": frozenset({"ENVIADA", "PERDIDA"}),
    "ENVIADA": frozenset({"GANADA", "PERDIDA", "VENCIDA"}),
    "VENCIDA": frozenset({"ENVIADA"}),
    "GANADA": frozenset(),
    "PERDIDA": frozenset(),
}

# Estados en los que el builder aún se puede editar (ítems/AIU/cabecera).
_EDITABLES = frozenset({"BORRADOR", "ENVIADA"})


@dataclass(frozen=True, slots=True)
class CotizacionArmada:
    """Cotización + sus ítems + su desglose AIU calculado (lo que consume la capa HTTP/Excel)."""

    cotizacion: CotizacionObra
    items: list[ItemCotizacionObra]
    totales: TotalesAIU


class CotizacionObraRepo(Protocol):
    """Puerto de datos del cotizador (lo implementa `SqlCotizacionObraRepository`)."""

    async def siguiente_numero(self, *, anio: int) -> str: ...
    async def obtener(self, cotizacion_id: int) -> CotizacionObra | None: ...
    async def items_de(self, cotizacion_id: int) -> list[ItemCotizacionObra]: ...
    async def listar(
        self, *, estado: str | None = None, cliente_id: int | None = None
    ) -> list[tuple[CotizacionObra, list[ItemCotizacionObra]]]: ...
    async def crear(self, datos: CotizacionObraCrear, *, numero: str) -> CotizacionObra: ...
    async def actualizar_cabecera(self, cotizacion: CotizacionObra, cambios: dict) -> CotizacionObra: ...
    async def reemplazar_items(self, cotizacion_id: int, items: list) -> None: ...
    async def cambiar_estado(self, cotizacion: CotizacionObra, nuevo_estado: str) -> CotizacionObra: ...


class ObrasConversion(Protocol):
    """Puerto de conversión a obra: lo satisface `ObrasService.crear_desde_cotizacion` (aditivo).

    DEBE ser idempotente: convertir dos veces la misma cotización devuelve la MISMA obra (la FK
    `obras.cotizacion_id` es UNIQUE), nunca crea una segunda.
    """

    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra: ...


class CotizacionObraService:
    def __init__(self, repo: CotizacionObraRepo, obras: ObrasConversion) -> None:
        self._repo = repo
        self._obras = obras

    def _totales(self, cotizacion: CotizacionObra, items: list[ItemCotizacionObra]) -> TotalesAIU:
        """Desglose AIU por la función pura (nunca se recalcula a mano)."""
        return calcular_totales_cotizacion(
            items,
            administracion_pct=cotizacion.administracion_pct,
            imprevistos_pct=cotizacion.imprevistos_pct,
            utilidad_pct=cotizacion.utilidad_pct,
            iva_sobre_utilidad_pct=cotizacion.iva_sobre_utilidad_pct,
        )

    async def crear(self, datos: CotizacionObraCrear) -> CotizacionArmada:
        """Da de alta una cotización (borrador). Autogenera el consecutivo `PIM-0XX-AAAA` si no se envió."""
        numero = datos.numero or await self._repo.siguiente_numero(anio=today_co().year)
        cotizacion = await self._repo.crear(datos, numero=numero)
        items = await self._repo.items_de(cotizacion.id)
        return CotizacionArmada(cotizacion, items, self._totales(cotizacion, items))

    async def obtener(self, cotizacion_id: int) -> CotizacionArmada:
        """Detalle completo. 404 si no existe."""
        cotizacion = await self._cargar(cotizacion_id)
        items = await self._repo.items_de(cotizacion_id)
        return CotizacionArmada(cotizacion, items, self._totales(cotizacion, items))

    async def listar(
        self, *, estado: str | None = None, cliente_id: int | None = None
    ) -> list[CotizacionArmada]:
        """Cotizaciones (más recientes primero), filtrables por estado y cliente, con su total."""
        filas = await self._repo.listar(estado=estado, cliente_id=cliente_id)
        return [CotizacionArmada(c, items, self._totales(c, items)) for c, items in filas]

    async def actualizar(
        self, cotizacion_id: int, datos: CotizacionObraActualizar
    ) -> CotizacionArmada:
        """Edita el builder (cabecera + ítems). 404 si no existe; 409 si el estado no admite edición."""
        cotizacion = await self._cargar(cotizacion_id)
        if cotizacion.estado not in _EDITABLES:
            raise CotizacionNoEditable(cotizacion.estado)
        cambios = datos.model_dump(exclude_unset=True, exclude={"items"})
        if cambios:
            await self._repo.actualizar_cabecera(cotizacion, cambios)
        if datos.items is not None:
            await self._repo.reemplazar_items(cotizacion_id, datos.items)
        items = await self._repo.items_de(cotizacion_id)
        return CotizacionArmada(cotizacion, items, self._totales(cotizacion, items))

    async def cambiar_estado(self, cotizacion_id: int, nuevo_estado: str) -> CotizacionArmada:
        """Aplica una transición VÁLIDA. 404 si no existe; 409 si la transición no se permite."""
        cotizacion = await self._cargar(cotizacion_id)
        if nuevo_estado not in _TRANSICIONES.get(cotizacion.estado, frozenset()):
            raise TransicionEstadoInvalida(cotizacion.estado, nuevo_estado)
        await self._repo.cambiar_estado(cotizacion, nuevo_estado)
        items = await self._repo.items_de(cotizacion_id)
        return CotizacionArmada(cotizacion, items, self._totales(cotizacion, items))

    async def convertir_a_obra(self, cotizacion_id: int) -> Obra:
        """Convierte una cotización GANADA en Obra (1-1). 404 si no existe; 409 si no está GANADA.

        Idempotente: la creación la hace `modules.obra` capturando la UNIQUE de `cotizacion_id`;
        convertir dos veces devuelve la MISMA obra, no duplica.
        """
        cotizacion = await self._cargar(cotizacion_id)
        if cotizacion.estado != "GANADA":
            raise CotizacionNoGanada(cotizacion.estado)
        return await self._obras.crear_desde_cotizacion(cotizacion)

    async def _cargar(self, cotizacion_id: int) -> CotizacionObra:
        cotizacion = await self._repo.obtener(cotizacion_id)
        if cotizacion is None:
            raise CotizacionInexistente(cotizacion_id)
        return cotizacion
