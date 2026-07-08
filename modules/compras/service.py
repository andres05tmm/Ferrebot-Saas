"""Servicio de compras: orquesta get-or-create de proveedor, cálculo de total y registro.

Lógica de dominio (sin SQL): resuelve el proveedor, calcula el total en el SERVIDOR (Σ cantidad×costo)
y delega el registro transaccional (stock + costo + eventos) en el repositorio. La fecha y el rango
default usan hora Colombia (regla #4).
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from core.config.timezone import COLOMBIA_TZ, now_co, rango_dia_co, today_co
from core.money import cuantizar
from modules.compras.errors import IdempotenciaConflicto
from modules.compras.repository import (
    AnalisisPrecioRow,
    CompraIdempotente,
    ItemCompra,
    SqlComprasRepository,
)
from modules.compras.schemas import AnalisisPrecioProveedor, CompraCrear, CompraLeer
from services.calculations.resbalos import Resbalo, calcular_resbalo

# Ventana por defecto del análisis de precios de proveedor (mismo semestre de la alerta de precio).
_VENTANA_ANALISIS = timedelta(days=182)

# Ventana del historial de precios del proveedor (spec 10: "promedio de los últimos 6 meses").
_VENTANA_PRECIO_PROVEEDOR = timedelta(days=182)
# Umbral de la alerta de precio: un costo unitario > 15% sobre el promedio histórico dispara la señal.
_UMBRAL_PRECIO_PROVEEDOR = Decimal("1.15")


@dataclass(frozen=True, slots=True)
class ResultadoCompra:
    """Compra registrada + si fue un replay idempotente (la key ya existía con el mismo payload)."""

    compra: CompraLeer
    replay: bool


def _mismo_payload(
    existente: CompraIdempotente,
    items: list[ItemCompra],
    total: Decimal,
    proveedor_id: int | None,
    *,
    obra_id: int | None,
    es_viaje_material: bool,
) -> bool:
    """True si la compra previa (misma key) coincide con el payload entrante (líneas + total + prov. +
    imputación de obra).

    Compara la sustancia económica (cada línea producto/cantidad/costo y el total) Y la IMPUTACIÓN de la
    compra: `obra_id` y `es_viaje_material` cambian qué es la compra (si mueve stock o se imputa a una obra,
    y con qué margen), así que reusar una key con esos campos distintos es un conflicto, no un replay. El
    proveedor solo entra si el entrante lo dio por `id` explícito (resolver nombre/nit aquí crearía un
    proveedor antes de saber si es replay). Decimales comparan por valor: 10.000 == 10."""
    if total != existente.total:
        return False
    # Orden estable aun con `producto_id` NULL (viajes de material / imputadas a obra): None → -1 para no
    # comparar None con int al ordenar.
    def _clave(t: tuple) -> tuple:
        return (t[0] if t[0] is not None else -1, t[1], t[2])

    actuales = sorted(((it.producto_id, it.cantidad, it.costo) for it in items), key=_clave)
    previos = sorted(existente.items, key=_clave)
    if actuales != previos:
        return False
    if proveedor_id is not None and proveedor_id != existente.compra.proveedor_id:
        return False
    # Imputación: obra distinta o cambiar el marcador de viaje de material NO es el mismo payload.
    if obra_id != existente.compra.obra_id:
        return False
    if es_viaje_material != existente.compra.es_viaje_material:
        return False
    return True


def _enriquecer_resbalo(compra: CompraLeer) -> CompraLeer:
    """Completa `resbalo`/`resbalo_pct`/`resbalo_alerta` de una compra de viaje con `calcular_resbalo`.

    El costo del viaje ES `compra.total` (Σ cantidad×costo); recalcular desde la función pura mantiene UNA
    sola verdad del margen (el monto persistido y el % del reporte no divergen)."""
    if compra.precio_venta_cliente is None:
        return compra
    r = calcular_resbalo(compra.precio_venta_cliente, compra.total)
    return compra.model_copy(update={
        # Conserva el monto PERSISTIDO (MONEY4, 4 decimales); solo deriva % y alerta desde la función pura.
        "resbalo": compra.resbalo if compra.resbalo is not None else r.monto,
        "resbalo_pct": r.porcentaje,
        "resbalo_alerta": r.alerta,
    })


def _analisis_a_schema(f: AnalisisPrecioRow) -> AnalisisPrecioProveedor:
    """Deriva variación % y alerta de una fila del análisis (misma verdad que la alerta de precio 15%).

    `variacion_pct` = cuánto por encima del promedio ponderado quedó el costo máximo del período; `alerta`
    se dispara cuando ese máximo supera el umbral (>15%). Sin promedio (>0) no hay base: variación 0, sin
    alerta (default seguro)."""
    prom = f.costo_unitario_promedio
    if prom > 0:
        variacion = ((f.costo_unitario_max - prom) / prom * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        alerta = f.costo_unitario_max > prom * _UMBRAL_PRECIO_PROVEEDOR
    else:
        variacion = Decimal("0.00")
        alerta = False
    return AnalisisPrecioProveedor(
        proveedor_id=f.proveedor_id,
        proveedor_nombre=f.proveedor_nombre,
        categoria=f.categoria,
        n_compras=f.n_compras,
        cantidad_total=f.cantidad_total,
        costo_unitario_promedio=cuantizar(prom),
        costo_unitario_min=cuantizar(f.costo_unitario_min),
        costo_unitario_max=cuantizar(f.costo_unitario_max),
        variacion_pct=variacion,
        alerta=alerta,
    )


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
                if not _mismo_payload(
                    existente, items, total, datos.proveedor.id,
                    obra_id=datos.obra_id, es_viaje_material=datos.es_viaje_material,
                ):
                    raise IdempotenciaConflicto(datos.idempotency_key)
                return ResultadoCompra(compra=existente.compra, replay=True)

        fecha = _fecha_compra(datos.fecha)
        proveedor_id = await self._repo.get_or_create_proveedor(
            proveedor_id=datos.proveedor.id, nombre=datos.proveedor.nombre, nit=datos.proveedor.nit,
        )
        # Resbalo del viaje de material (spec 11): solo cuando aplica. `total` ES el costo del viaje.
        resbalo: Resbalo | None = None
        if datos.es_viaje_material and datos.precio_venta_cliente is not None:
            resbalo = calcular_resbalo(datos.precio_venta_cliente, total)
        # Alerta de precio de proveedor (spec 10): se calcula ANTES de insertar la compra nueva (que no
        # entra a su propia ventana histórica).
        alerta_precio = await self._alerta_precio_proveedor(
            proveedor_id, items=items, total=total, categoria=datos.categoria, hasta=fecha,
        )
        compra = await self._repo.crear_compra(
            proveedor_id=proveedor_id, fecha=fecha,
            items=items, total=total, usuario_id=usuario_id,
            idempotency_key=datos.idempotency_key,
            obra_id=datos.obra_id, categoria=datos.categoria,
            es_viaje_material=datos.es_viaje_material,
            precio_venta_cliente=datos.precio_venta_cliente,
            resbalo=resbalo.monto if resbalo is not None else None,
            factura_url=datos.factura_url,
        )
        if self._retenciones is not None:
            # Retenciones inline (ADR 0027): calcula/persiste los renglones en la MISMA transacción
            # (commit=False), atómico con la compra. Sin config activa no crea renglones (opt-in).
            await self._retenciones.aplicar_a_compra(compra.id, commit=False)
        # Derivados de salida (no persistidos): % y alertas para que el cliente/bot avise al dueño.
        compra = compra.model_copy(update={
            "resbalo_pct": resbalo.porcentaje if resbalo is not None else None,
            "resbalo_alerta": resbalo.alerta if resbalo is not None else False,
            "alerta_precio_proveedor": alerta_precio,
        })
        return ResultadoCompra(compra=compra, replay=False)

    async def _alerta_precio_proveedor(
        self,
        proveedor_id: int,
        *,
        items: list[ItemCompra],
        total: Decimal,
        categoria: str | None,
        hasta: datetime,
    ) -> bool:
        """True si el costo unitario de esta compra supera en >15% el promedio de 6 meses del proveedor.

        El costo unitario de la compra se toma ponderado (total / Σ cantidad). Sin cantidad o sin
        historial no hay señal (default seguro: no alarmar sin base de comparación)."""
        cantidad_total = sum((it.cantidad for it in items), Decimal("0"))
        if cantidad_total <= 0:
            return False
        costo_unitario = total / cantidad_total
        promedio = await self._repo.promedio_costo_unitario_proveedor(
            proveedor_id, desde=hasta - _VENTANA_PRECIO_PROVEEDOR, hasta=hasta, categoria=categoria,
        )
        if promedio is None or promedio <= 0:
            return False
        return costo_unitario > promedio * _UMBRAL_PRECIO_PROVEEDOR

    async def listar(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Compras del rango (default mes en curso, hora Colombia)."""
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.listar(inicio=inicio, fin=fin)

    async def reporte_resbalos(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Reporte de resbalos (spec 11): viajes de material del rango con margen $ y % + alerta."""
        inicio, fin = _rango_o_mes(desde, hasta)
        compras = await self._repo.listar_resbalos(inicio=inicio, fin=fin)
        return [_enriquecer_resbalo(c) for c in compras]

    async def analisis_precios(
        self,
        *,
        desde: date | None,
        hasta: date | None,
        proveedor_id: int | None = None,
        categoria: str | None = None,
    ) -> list[AnalisisPrecioProveedor]:
        """Análisis de precios de proveedor (Fase 8, spec 10): costo unitario ponderado por (proveedor,
        categoría) en el período, con su rango y la alerta de sobreprecio. Default: los últimos 6 meses
        (hora Colombia), la misma ventana de la alerta de precio."""
        hoy = today_co()
        inicio, fin = rango_dia_co(desde or (hoy - _VENTANA_ANALISIS), hasta or hoy)
        filas = await self._repo.analisis_precios_proveedor(
            desde=inicio, hasta=fin, proveedor_id=proveedor_id, categoria=categoria,
        )
        return [_analisis_a_schema(f) for f in filas]
