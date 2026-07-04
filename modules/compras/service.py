"""Servicio de compras: orquesta get-or-create de proveedor, cálculo de total y registro.

Lógica de dominio (sin SQL): resuelve el proveedor, calcula el total en el SERVIDOR (Σ cantidad×costo)
y delega el registro transaccional (stock + costo + eventos) en el repositorio. La fecha y el rango
default usan hora Colombia (regla #4).
"""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol

from core.config.timezone import COLOMBIA_TZ, now_co, rango_dia_co, today_co
from core.money import cuantizar
from modules.compras.errors import IdempotenciaConflicto
from modules.compras.repository import (
    CompraIdempotente,
    ItemCompra,
    SqlComprasRepository,
)
from modules.compras.schemas import CompraCrear, CompraLeer


@dataclass(frozen=True, slots=True)
class ResultadoCompra:
    """Compra registrada + si fue un replay idempotente (la key ya existía con el mismo payload)."""

    compra: CompraLeer
    replay: bool


def _mismo_payload(
    existente: CompraIdempotente, items: list[ItemCompra], total: Decimal, proveedor_id: int | None
) -> bool:
    """True si la compra previa (misma key) coincide con el payload entrante (líneas + total + prov.).

    Compara la sustancia económica (cada línea producto/cantidad/costo y el total). El proveedor solo
    entra si el entrante lo dio por `id` explícito (resolver nombre/nit aquí crearía un proveedor antes
    de saber si es replay). Decimales comparan por valor: 10.000 == 10."""
    if total != existente.total:
        return False
    actuales = sorted((it.producto_id, it.cantidad, it.costo) for it in items)
    previos = sorted(existente.items)
    if actuales != previos:
        return False
    if proveedor_id is not None and proveedor_id != existente.compra.proveedor_id:
        return False
    return True


def _fecha_compra(fecha: date | None) -> datetime:
    """Fecha de la compra como datetime aware Colombia: la dada (mediodía) o ahora."""
    if fecha is None:
        return now_co()
    return datetime.combine(fecha, time(12, 0), tzinfo=COLOMBIA_TZ)


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    """Ventana [inicio, fin] aware: rango dado o, si falta, el mes en curso (día 1 → hoy Colombia)."""
    hoy = today_co()
    return rango_dia_co(desde or hoy.replace(day=1), hasta or hoy)


class RetencionesAplicador(Protocol):
    """Puerto del motor de retenciones (lo cumple RetencionesService). Estructural, opcional.

    En la compra NOSOTROS somos agente retenedor: al registrarla se calculan/persisten las retenciones
    practicadas (ADR 0027) inline, en la MISMA transacción (`commit=False`).
    """

    async def aplicar_a_compra(self, compra_id: int, *, commit: bool = ...) -> object | None: ...


class ComprasService:
    def __init__(
        self, repo: SqlComprasRepository, *, retenciones: RetencionesAplicador | None = None
    ) -> None:
        self._repo = repo
        # Motor de retenciones inline (opt-in, ADR 0027): solo se inyecta con la feature `retenciones`.
        self._retenciones = retenciones

    async def registrar(self, datos: CompraCrear, *, usuario_id: int | None) -> ResultadoCompra:
        """Registra la compra: resuelve proveedor, calcula total y persiste (stock + costo + eventos).

        Idempotente (ai-tools.md §4): si `idempotency_key` ya existe con el MISMO payload, devuelve la
        compra original sin re-registrar (replay) y SIN resolver proveedor (no crea un proveedor en el
        camino de replay); con payload distinto → `IdempotenciaConflicto`. El índice UNIQUE parcial
        (0025) es el respaldo estructural ante una carrera.
        """
        items = [
            ItemCompra(producto_id=it.producto_id, cantidad=it.cantidad, costo=it.costo)
            for it in datos.items
        ]
        total = cuantizar(sum((it.cantidad * it.costo for it in items), Decimal("0")))

        if datos.idempotency_key:
            existente = await self._repo.buscar_por_idempotency(datos.idempotency_key)
            if existente is not None:
                if not _mismo_payload(existente, items, total, datos.proveedor.id):
                    raise IdempotenciaConflicto(datos.idempotency_key)
                return ResultadoCompra(compra=existente.compra, replay=True)

        proveedor_id = await self._repo.get_or_create_proveedor(
            proveedor_id=datos.proveedor.id, nombre=datos.proveedor.nombre, nit=datos.proveedor.nit,
        )
        compra = await self._repo.crear_compra(
            proveedor_id=proveedor_id, fecha=_fecha_compra(datos.fecha),
            items=items, total=total, usuario_id=usuario_id,
            idempotency_key=datos.idempotency_key,
        )
        if self._retenciones is not None:
            # Retenciones inline (ADR 0027): calcula/persiste los renglones en la MISMA transacción
            # (commit=False), atómico con la compra. Sin config activa no crea renglones (opt-in).
            await self._retenciones.aplicar_a_compra(compra.id, commit=False)
        return ResultadoCompra(compra=compra, replay=False)

    async def listar(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Compras del rango (default mes en curso, hora Colombia)."""
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.listar(inicio=inicio, fin=fin)
