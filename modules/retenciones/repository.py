"""Repositorio de retenciones/INC: único lugar con SQL del módulo (regla no negociable #2).

Cubre el catálogo editable (`config_retenciones`) y la persistencia idempotente de renglones por
documento (`retenciones_documento`), más la lectura de las bases del documento (venta/compra). Todo
sobre la sesión del tenant (la base ES la frontera). Idempotencia por UPSERT (ON CONFLICT) sobre las
claves naturales, para que reaplicar el motor no duplique filas.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.retenciones.models import ConfigRetencion, RetencionDocumento
from modules.retenciones.motor import UVT, ReglaRetencion, RetencionCalculada


@dataclass(frozen=True, slots=True)
class BaseDocumento:
    """Bases tributarias de un documento: gravable (sin IVA), IVA y total cobrado/facturado."""

    base_gravable: Decimal
    iva: Decimal
    total: Decimal


class SqlRetencionesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Catálogo (config_retenciones) ────────────────────────────────────────
    async def listar_config(self) -> list[ConfigRetencion]:
        """Todas las reglas del tenant, orden estable (tipo, concepto)."""
        return list(
            (
                await self._s.execute(
                    select(ConfigRetencion).order_by(ConfigRetencion.tipo, ConfigRetencion.concepto)
                )
            ).scalars()
        )

    async def upsert_config(
        self, *, tipo: str, concepto: str, base_minima_uvt: Decimal, tarifa: Decimal, activo: bool
    ) -> ConfigRetencion:
        """Alta o edición de una regla por su clave natural (tipo, concepto). Idempotente.

        En conflicto actualiza tarifa/base/activo y refresca `actualizado_en`; NO toca `editable`
        (lo fija la semilla/super-admin). Devuelve la fila resultante.
        """
        stmt = (
            pg_insert(ConfigRetencion)
            .values(
                tipo=tipo, concepto=concepto, base_minima_uvt=base_minima_uvt,
                tarifa=tarifa, activo=activo,
            )
            .on_conflict_do_update(
                constraint="uq_config_retenciones_tipo_concepto",
                set_={
                    "base_minima_uvt": base_minima_uvt,
                    "tarifa": tarifa,
                    "activo": activo,
                    "actualizado_en": text("now()"),
                },
            )
            .returning(ConfigRetencion.id)
        )
        rid = (await self._s.execute(stmt)).scalar_one()
        await self._s.commit()
        return (
            await self._s.execute(select(ConfigRetencion).where(ConfigRetencion.id == rid))
        ).scalar_one()

    async def reglas_activas(self) -> list[ReglaRetencion]:
        """Reglas activas del tenant proyectadas al motor (sin la fila `uvt`)."""
        filas = (
            await self._s.execute(
                select(ConfigRetencion).where(
                    ConfigRetencion.activo.is_(True), ConfigRetencion.tipo != UVT
                )
            )
        ).scalars()
        return [
            ReglaRetencion(
                tipo=f.tipo, concepto=f.concepto,
                base_minima_uvt=f.base_minima_uvt or Decimal("0"),
                tarifa=f.tarifa or Decimal("0"), activo=True,
            )
            for f in filas
        ]

    async def uvt_valor(self) -> Decimal:
        """Valor del UVT en pesos (fila `tipo='uvt'` más reciente por concepto/año). 0 si no hay.

        0 hace que el umbral de retefuente se ignore (la retención aplica sin comparar la base mínima).
        """
        val = (
            await self._s.execute(
                select(ConfigRetencion.tarifa)
                .where(ConfigRetencion.tipo == UVT, ConfigRetencion.activo.is_(True))
                .order_by(ConfigRetencion.concepto.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return Decimal(val) if val is not None else Decimal("0")

    # ── Bases del documento ──────────────────────────────────────────────────
    async def base_venta(self, venta_id: int) -> BaseDocumento | None:
        """Bases de una venta COMPLETADA (subtotal sin IVA, IVA, total). None si no existe/anulada."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT subtotal, impuestos, total FROM ventas "
                    "WHERE id = :id AND estado = 'completada'"
                ),
                {"id": venta_id},
            )
        ).first()
        if fila is None:
            return None
        return BaseDocumento(
            base_gravable=Decimal(fila[0]), iva=Decimal(fila[1]), total=Decimal(fila[2])
        )

    async def base_compra(self, compra_id: int) -> BaseDocumento | None:
        """Bases de una compra. Usa el desglose de `compras_fiscal` (base/IVA) si existe; si no, toma el
        total de la compra como base gravable con IVA 0. None si la compra no existe."""
        total = (
            await self._s.execute(
                text("SELECT total FROM compras WHERE id = :id"), {"id": compra_id}
            )
        ).scalar_one_or_none()
        if total is None:
            return None
        fiscal = (
            await self._s.execute(
                text(
                    "SELECT base, iva FROM compras_fiscal WHERE compra_id = :id "
                    "ORDER BY id LIMIT 1"
                ),
                {"id": compra_id},
            )
        ).first()
        if fiscal is not None and fiscal[0] is not None:
            return BaseDocumento(
                base_gravable=Decimal(fiscal[0]),
                iva=Decimal(fiscal[1]) if fiscal[1] is not None else Decimal("0"),
                total=Decimal(total),
            )
        return BaseDocumento(
            base_gravable=Decimal(total), iva=Decimal("0"), total=Decimal(total)
        )

    # ── Persistencia de renglones (retenciones_documento) ────────────────────
    async def guardar_renglones(
        self, *, doc_tipo: str, doc_id: int, renglones: list[RetencionCalculada]
    ) -> None:
        """UPSERT idempotente de los renglones del documento (clave doc_tipo,doc_id,tipo,concepto).

        Reaplicar el motor sobre el mismo documento ACTUALIZA en el lugar (no duplica). Commitea.
        """
        for r in renglones:
            stmt = (
                pg_insert(RetencionDocumento)
                .values(
                    doc_tipo=doc_tipo, doc_id=doc_id, tipo=r.tipo, concepto=r.concepto,
                    base=r.base, tarifa=r.tarifa, valor=r.valor,
                )
                .on_conflict_do_update(
                    constraint="uq_retenciones_documento_doc",
                    set_={"base": r.base, "tarifa": r.tarifa, "valor": r.valor},
                )
            )
            await self._s.execute(stmt)
        await self._s.commit()

    async def listar_por_documento(
        self, *, doc_tipo: str, doc_id: int
    ) -> list[RetencionDocumento]:
        """Renglones persistidos de un documento, orden estable (tipo, concepto)."""
        return list(
            (
                await self._s.execute(
                    select(RetencionDocumento)
                    .where(
                        RetencionDocumento.doc_tipo == doc_tipo,
                        RetencionDocumento.doc_id == doc_id,
                    )
                    .order_by(RetencionDocumento.tipo, RetencionDocumento.concepto)
                )
            ).scalars()
        )
