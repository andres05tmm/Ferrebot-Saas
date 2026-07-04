"""Servicio de retenciones/INC (ADR 0027): catálogo editable + aplicación del motor a documentos.

Delgado sobre el motor PURO (`modules.retenciones.motor`) y el repositorio (SQL). Aplicar el motor a
una venta/compra calcula y PERSISTE los renglones tributarios SIN tocar el total del documento: la
retención se refleja como menor pago recibido (`neto_a_recibir`), nunca como menor venta. Opt-in: sin
reglas configuradas el resultado es vacío y ningún total cambia.
"""
from __future__ import annotations

from decimal import Decimal

from modules.retenciones.motor import (
    calcular_retenciones,
    total_inc,
    total_retenido,
)
from modules.retenciones.repository import SqlRetencionesRepository
from modules.retenciones.schemas import (
    ReglaLeer,
    ReglaUpsert,
    ResumenRetenciones,
    RetencionLeer,
    TIPOS_VALIDOS,
)

DOC_VENTA = "venta"
DOC_COMPRA = "compra"


class TipoRetencionInvalido(ValueError):
    """El `tipo` de la regla no es uno de los conocidos (retefuente/ica/reteiva/inc/uvt)."""


class RetencionesService:
    def __init__(self, repo: SqlRetencionesRepository) -> None:
        self._repo = repo

    # ── Catálogo ─────────────────────────────────────────────────────────────
    async def listar_config(self) -> list[ReglaLeer]:
        """Catálogo tributario del tenant (todas las reglas)."""
        return [self._a_regla_leer(c) for c in await self._repo.listar_config()]

    async def upsert_regla(self, datos: ReglaUpsert) -> ReglaLeer:
        """Alta/edición de una regla por (tipo, concepto). Valida el `tipo` contra el catálogo conocido."""
        if datos.tipo not in TIPOS_VALIDOS:
            raise TipoRetencionInvalido(datos.tipo)
        fila = await self._repo.upsert_config(
            tipo=datos.tipo, concepto=datos.concepto.strip(),
            base_minima_uvt=datos.base_minima_uvt, tarifa=datos.tarifa, activo=datos.activo,
        )
        return self._a_regla_leer(fila)

    # ── Aplicación a documentos ──────────────────────────────────────────────
    async def aplicar_a_venta(self, venta_id: int) -> ResumenRetenciones | None:
        """Calcula y persiste los renglones tributarios de una venta. None si no existe/anulada."""
        base = await self._repo.base_venta(venta_id)
        if base is None:
            return None
        return await self._aplicar(DOC_VENTA, venta_id, base.base_gravable, base.iva, base.total)

    async def aplicar_a_compra(self, compra_id: int) -> ResumenRetenciones | None:
        """Calcula y persiste los renglones tributarios de una compra. None si la compra no existe."""
        base = await self._repo.base_compra(compra_id)
        if base is None:
            return None
        return await self._aplicar(DOC_COMPRA, compra_id, base.base_gravable, base.iva, base.total)

    async def obtener_documento(self, *, doc_tipo: str, doc_id: int) -> ResumenRetenciones:
        """Lee los renglones ya persistidos de un documento y arma el resumen (sin recalcular)."""
        filas = await self._repo.listar_por_documento(doc_tipo=doc_tipo, doc_id=doc_id)
        renglones = [
            RetencionLeer(tipo=f.tipo, concepto=f.concepto, base=f.base, tarifa=f.tarifa, valor=f.valor)
            for f in filas
        ]
        base = None
        if doc_tipo == DOC_VENTA:
            base = await self._repo.base_venta(doc_id)
        elif doc_tipo == DOC_COMPRA:
            base = await self._repo.base_compra(doc_id)
        total_doc = base.total if base is not None else Decimal("0")
        ret = _suma(renglones, {"retefuente", "ica", "reteiva"})
        inc = _suma(renglones, {"inc"})
        return ResumenRetenciones(
            doc_tipo=doc_tipo, doc_id=doc_id, total_documento=total_doc,
            total_retenido=ret, total_inc=inc, neto_a_recibir=total_doc - ret, retenciones=renglones,
        )

    async def _aplicar(
        self, doc_tipo: str, doc_id: int, base_gravable: Decimal, iva: Decimal, total: Decimal
    ) -> ResumenRetenciones:
        reglas = await self._repo.reglas_activas()
        uvt = await self._repo.uvt_valor()
        calculadas = calcular_retenciones(
            reglas, base_gravable=base_gravable, iva=iva, uvt_valor=uvt
        )
        await self._repo.guardar_renglones(doc_tipo=doc_tipo, doc_id=doc_id, renglones=calculadas)
        ret = total_retenido(calculadas)
        inc = total_inc(calculadas)
        return ResumenRetenciones(
            doc_tipo=doc_tipo, doc_id=doc_id, total_documento=total,
            total_retenido=ret, total_inc=inc, neto_a_recibir=total - ret,
            retenciones=[
                RetencionLeer(tipo=r.tipo, concepto=r.concepto, base=r.base, tarifa=r.tarifa, valor=r.valor)
                for r in calculadas
            ],
        )

    @staticmethod
    def _a_regla_leer(c) -> ReglaLeer:
        return ReglaLeer(
            id=c.id, tipo=c.tipo, concepto=c.concepto, base_minima_uvt=c.base_minima_uvt,
            tarifa=c.tarifa, activo=c.activo, editable=c.editable, actualizado_en=c.actualizado_en,
        )


def _suma(renglones: list[RetencionLeer], tipos: set[str]) -> Decimal:
    from core.money import cuantizar

    return cuantizar(sum((r.valor for r in renglones if r.tipo in tipos), Decimal("0")))
