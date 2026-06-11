"""Servicio del frente de pagos (ADR 0013): crear solicitudes de cobro y conciliar su estado.

Transversal: los packs lo consumen (pedido confirmado → link; anticipo de cita; saldo de cobranza).
Con PSP (puerto inyectado) crea el link real; sin PSP cae a `manual` (etiqueta "pendiente de pago",
el negocio concilia a mano con `marcar_pagado_manual`). Idempotente por (origen, origen_id) y por
`referencia`. La conciliación (polling) corre en el worker — patrón reconciliar_pendientes.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from core.pagos.ports import PagosPort, SolicitudCobro
from modules.pagos.models import Cobro
from modules.pagos.repository import SqlPagosRepository


class CobroInexistente(Exception):
    """El cobro no existe (dashboard)."""


@dataclass(frozen=True, slots=True)
class ResumenConciliacion:
    """Resultado de una corrida de conciliación: cuántos revisados y cuántos cambiaron de estado."""

    revisados: int = 0
    pagados: int = 0
    cerrados: int = 0   # vencidos + cancelados


class PagosService:
    def __init__(self, repo: SqlPagosRepository, *, psp: PagosPort | None = None) -> None:
        self._repo = repo
        # PSP OPCIONAL por tenant (Bold v1): None = modo manual (sin link, conciliación humana).
        self._psp = psp

    async def crear_cobro(
        self,
        *,
        origen: str,
        origen_id: int | None,
        monto: Decimal,
        descripcion: str,
        cliente_telefono: str | None = None,
        vence_en: datetime | None = None,
    ) -> Cobro:
        """Crea (o devuelve, idempotente) la solicitud de cobro de un objeto de dominio.

        Con PSP el cobro nace con link/URL real; sin PSP nace `manual` (el agente igual informa el
        total y el negocio concilia a mano). Nunca dos cobros para el mismo (origen, origen_id).
        """
        if origen_id is not None:
            existente = await self._repo.cobro_por_origen(origen, origen_id)
            if existente is not None:
                return existente
        referencia = f"{origen}-{origen_id or 'x'}-{uuid.uuid4().hex[:10]}"
        cobro = Cobro(
            referencia=referencia, origen=origen, origen_id=origen_id,
            cliente_telefono=cliente_telefono, monto=monto, descripcion=descripcion,
        )
        if self._psp is not None:
            link = await self._psp.crear_link(SolicitudCobro(
                referencia=referencia, monto=monto, descripcion=descripcion, vence_en=vence_en,
            ))
            cobro.proveedor = "bold"
            cobro.proveedor_id = link.proveedor_id
            cobro.url = link.url
        return await self._repo.crear(cobro)

    async def conciliar(self, *, limite: int = 100) -> ResumenConciliacion:
        """Barre los cobros `pendiente` del PSP y consulta su estado real (polling del worker).

        Sin PSP no hay nada que conciliar (los manuales los cierra el negocio). Un fallo de red en
        un cobro no tumba la corrida: se reintenta en la próxima.
        """
        if self._psp is None:
            return ResumenConciliacion()
        pagados = cerrados = revisados = 0
        for cobro in await self._repo.pendientes_de("bold", limite=limite):
            if not cobro.proveedor_id:
                continue
            try:
                estado = await self._psp.consultar(cobro.proveedor_id)
            except Exception:  # noqa: BLE001 — un cobro caído no frena el barrido
                continue
            revisados += 1
            if estado == "pendiente":
                continue
            await self._repo.marcar(cobro, estado)
            if estado == "pagado":
                pagados += 1
            else:
                cerrados += 1
        return ResumenConciliacion(revisados=revisados, pagados=pagados, cerrados=cerrados)

    # --- dashboard -------------------------------------------------------------
    async def listar(self, *, estados: list[str] | None = None) -> list[Cobro]:
        return await self._repo.listar(estados=estados)

    async def marcar_pagado_manual(self, cobro_id: int) -> Cobro:
        """El negocio vio la plata (transferencia directa/efectivo): cierra el cobro a mano."""
        cobro = await self._repo.cobro_por_id(cobro_id)
        if cobro is None:
            raise CobroInexistente(str(cobro_id))
        return await self._repo.marcar(cobro, "pagado")

    async def cancelar(self, cobro_id: int) -> Cobro:
        cobro = await self._repo.cobro_por_id(cobro_id)
        if cobro is None:
            raise CobroInexistente(str(cobro_id))
        return await self._repo.marcar(cobro, "cancelado")
