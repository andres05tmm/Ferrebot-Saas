"""Repositorio del cotizador AIU: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules.obra.repository` (sesión del tenant = la transacción; aquí no se hace commit). El ORM
de `CotizacionObra`/`ItemCotizacionObra` no declara `relationship` (patrón del repo: las FKs viven en
la migración), así que los ítems se leen/escriben con consultas explícitas y el listado los trae por
LOTE (un solo IN) para no incurrir en N+1.

Numeración consecutiva `PIM-0XX-AAAA` por AÑO (reinicia cada año). Como Fase 2 NO escribe migraciones
(no hay secuencia Postgres dedicada, a diferencia de facturación), el consecutivo se serializa con un
`pg_advisory_xact_lock(ns, año)` —igual idea que `modules.agenda.repository.lock_recurso`— que se
libera al COMMIT: dos altas concurrentes se encolan y la segunda ve el número de la primera → sin
huecos ni colisiones. La UNIQUE de `numero` es el respaldo último.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from modules.cotizacion_obra.errors import NumeroDuplicado
from modules.cotizacion_obra.schemas import (
    CotizacionObraCrear,
    ItemCotizacionObraCrear,
)
from modules.obra.models import CotizacionObra, ItemCotizacionObra

# Namespace del advisory lock de numeración (distinto del de agenda 0xA6E0). "COTI".
_LOCK_NS_COTIZACION = 0xC071
# Prefijo del consecutivo del vertical construcción (spec 03: "PIM-0XX-2026"). El `numero` es editable
# por cotización; esto es sólo el prefijo del AUTOGENERADO. Provisional hasta parametrizarlo por tenant.
PREFIJO_COTIZACION = "PIM"


class SqlCotizacionObraRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- numeración consecutiva -------------------------------------------------------
    async def siguiente_numero(self, *, anio: int, prefijo: str = PREFIJO_COTIZACION) -> str:
        """Reserva el siguiente `PIM-0XX-AAAA` del año, serializado por advisory xact lock.

        El lock (ns, año) se libera al COMMIT: mientras una alta lo tiene, otra alta del mismo año se
        encola; al liberarse, ésta relee el máximo YA committeado. Sólo se consideran los números con
        el formato canónico del prefijo/año (regex): un `numero` editado a mano a otro formato no
        rompe el `::int` (queda fuera de la serie automática, lo cual es correcto).
        """
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(:ns, :anio)"),
            {"ns": _LOCK_NS_COTIZACION, "anio": anio},
        )
        patron = f"^{prefijo}-[0-9]+-{anio}$"
        maximo = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(MAX(split_part(numero, '-', 2)::int), 0) "
                    "FROM cotizaciones_obra WHERE numero ~ :patron"
                ),
                {"patron": patron},
            )
        ).scalar_one()
        return f"{prefijo}-{int(maximo) + 1:03d}-{anio}"

    # --- lectura ----------------------------------------------------------------------
    async def obtener(self, cotizacion_id: int) -> CotizacionObra | None:
        return (
            await self._s.execute(
                select(CotizacionObra).where(CotizacionObra.id == cotizacion_id)
            )
        ).scalar_one_or_none()

    async def items_de(self, cotizacion_id: int) -> list[ItemCotizacionObra]:
        """Renglones de una cotización, en el orden del builder (`orden`, id como desempate)."""
        stmt = (
            select(ItemCotizacionObra)
            .where(ItemCotizacionObra.cotizacion_id == cotizacion_id)
            .order_by(ItemCotizacionObra.orden, ItemCotizacionObra.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def listar(
        self, *, estado: str | None = None, cliente_id: int | None = None
    ) -> list[tuple[CotizacionObra, list[ItemCotizacionObra]]]:
        """Cotizaciones (más recientes primero) con sus ítems traídos por LOTE (sin N+1)."""
        stmt = select(CotizacionObra)
        if estado is not None:
            stmt = stmt.where(CotizacionObra.estado == estado)
        if cliente_id is not None:
            stmt = stmt.where(CotizacionObra.cliente_id == cliente_id)
        stmt = stmt.order_by(CotizacionObra.creado_en.desc(), CotizacionObra.id.desc())
        cotizaciones = list((await self._s.execute(stmt)).scalars().all())
        if not cotizaciones:
            return []
        ids = [c.id for c in cotizaciones]
        items_todos = (
            await self._s.execute(
                select(ItemCotizacionObra)
                .where(ItemCotizacionObra.cotizacion_id.in_(ids))
                .order_by(ItemCotizacionObra.orden, ItemCotizacionObra.id)
            )
        ).scalars().all()
        por_cotizacion: dict[int, list[ItemCotizacionObra]] = {cid: [] for cid in ids}
        for item in items_todos:
            por_cotizacion[item.cotizacion_id].append(item)
        return [(c, por_cotizacion[c.id]) for c in cotizaciones]

    # --- escritura --------------------------------------------------------------------
    async def crear(self, datos: CotizacionObraCrear, *, numero: str) -> CotizacionObra:
        """Inserta la cotización + sus ítems. Traduce la colisión de `numero` (UNIQUE) a dominio."""
        cotizacion = CotizacionObra(
            numero=numero,
            cliente_id=datos.cliente_id,
            nombre_obra=datos.nombre_obra,
            ubicacion=datos.ubicacion,
            vigencia_dias=datos.vigencia_dias,
            administracion_pct=datos.administracion_pct,
            imprevistos_pct=datos.imprevistos_pct,
            utilidad_pct=datos.utilidad_pct,
            iva_sobre_utilidad_pct=datos.iva_sobre_utilidad_pct,
            condiciones=datos.condiciones,
        )
        self._s.add(cotizacion)
        try:
            await self._s.flush()  # asigna id y dispara la UNIQUE de `numero`
        except IntegrityError as exc:
            raise NumeroDuplicado(numero) from exc
        self._insertar_items(cotizacion.id, datos.items)
        await self._s.flush()
        return cotizacion

    async def actualizar_cabecera(self, cotizacion: CotizacionObra, cambios: dict) -> CotizacionObra:
        """Aplica un parche parcial de cabecera sobre una cotización ya cargada (sólo claves presentes)."""
        for campo, valor in cambios.items():
            setattr(cotizacion, campo, valor)
        await self._s.flush()
        return cotizacion

    async def reemplazar_items(
        self, cotizacion_id: int, items: list[ItemCotizacionObraCrear]
    ) -> None:
        """Semántica de builder: borra los renglones actuales y persiste el set completo entrante."""
        await self._s.execute(
            delete(ItemCotizacionObra).where(ItemCotizacionObra.cotizacion_id == cotizacion_id)
        )
        self._insertar_items(cotizacion_id, items)
        await self._s.flush()

    async def cambiar_estado(self, cotizacion: CotizacionObra, nuevo_estado: str) -> CotizacionObra:
        """Persiste el nuevo estado (la validación de la transición la hace el servicio)."""
        cotizacion.estado = nuevo_estado
        await self._s.flush()
        return cotizacion

    def _insertar_items(self, cotizacion_id: int, items: list[ItemCotizacionObraCrear]) -> None:
        for item in items:
            self._s.add(
                ItemCotizacionObra(
                    cotizacion_id=cotizacion_id,
                    orden=item.orden,
                    descripcion=item.descripcion,
                    unidad=item.unidad,
                    cantidad=item.cantidad,
                    valor_unitario=item.valor_unitario,
                    costo_material_est=item.costo_material_est,
                    costo_mano_obra_est=item.costo_mano_obra_est,
                    costo_equipo_est=item.costo_equipo_est,
                )
            )


def subtotal_item(item: ItemCotizacionObra) -> Decimal:
    """Subtotal de un renglón (cantidad × valor_unitario), sin cuantizar (se muestra tal cual)."""
    return item.cantidad * item.valor_unitario
