"""Notas electrónicas crédito/débito (ADR 0026, Fase 3 Contable B).

Archivo NUEVO y autocontenido (no toca `service.py`/`repository.py`, que la Fase 4 también edita):
persiste la nota en `notas_electronicas`, la emite por el pipeline MATIAS existente (reusa el enum
`fe_tipo` y `EmisionResultado`) y deja la bitácora en `eventos_dian`. Idempotente por `idempotency_key`.

- **Nota crédito**: corrige a la BAJA una venta ya transmitida a DIAN (una devolución total/parcial). Es
  la vía OBLIGATORIA cuando la venta fue facturada: el borrado físico queda solo para ventas no
  transmitidas (lo bloquea el guard de ventas).
- **Nota débito**: ajusta al ALZA una venta facturada (mismo pipeline, `fe_tipo='nota_debito'`).

La construcción del UBL fino de la nota (referencia a la factura, motivo DIAN) se confirma contra el
sandbox MATIAS en una fase posterior; aquí el payload es mínimo y los tests usan los fakes existentes
(NUNCA el MATIAS real). El estado que persiste espeja la emisión de facturas: pendiente → aceptada |
rechazada | error.
"""
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from core.logging import get_logger
from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.models import EventoDian, NotaElectronica
from modules.facturacion.service import ConfigFiscal

log = get_logger("facturacion.notas")


class NotaLeer(BaseModel):
    """Vista de salida de una nota electrónica (mapea el ORM `NotaElectronica`)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    factura_id: int | None
    venta_id: int | None
    tipo: str
    motivo: str | None
    prefijo: str | None
    consecutivo: int | None
    cufe: str | None
    estado: str
    idempotency_key: str | None
    intentos: int
    creado_en: datetime | None = None


class NotasRepo(Protocol):
    """Puerto de datos de notas (lo implementa `SqlNotasRepository`; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> NotaLeer | None: ...
    async def crear_pendiente(
        self, *, tipo: str, venta_id: int | None, factura_id: int | None,
        motivo: str | None, prefijo: str | None, idempotency_key: str,
    ) -> NotaLeer: ...
    async def marcar_aceptada(self, nota_id: int, *, cufe: str, dian_respuesta: dict) -> NotaLeer: ...
    async def marcar_rechazada(self, nota_id: int, *, error_msg: str, dian_respuesta: dict) -> NotaLeer: ...
    async def marcar_error(self, nota_id: int, *, error_msg: str) -> NotaLeer: ...
    async def registrar_evento(
        self, factura_id: int | None, *, evento: str, estado: str, payload: dict
    ) -> None: ...


class NotasService:
    """Emite notas crédito/débito por el pipeline MATIAS existente. Idempotente por `idempotency_key`."""

    def __init__(
        self, repo: NotasRepo, matias=None, config: ConfigFiscal | None = None
    ) -> None:
        self._repo = repo
        self._matias = matias
        self._config = config

    async def emitir_nota_credito(
        self, *, venta_id: int, factura_id: int | None, total: Decimal,
        motivo: str | None, idempotency_key: str,
    ) -> NotaLeer:
        """Nota crédito (corrección a la baja) sobre una venta ya transmitida a DIAN."""
        return await self._emitir(
            "nota_credito", venta_id=venta_id, factura_id=factura_id, total=total,
            motivo=motivo, key=idempotency_key,
        )

    async def emitir_nota_debito(
        self, *, venta_id: int, factura_id: int | None, total: Decimal,
        motivo: str | None, idempotency_key: str,
    ) -> NotaLeer:
        """Nota débito (ajuste al alza) sobre una venta facturada (mismo pipeline)."""
        return await self._emitir(
            "nota_debito", venta_id=venta_id, factura_id=factura_id, total=total,
            motivo=motivo, key=idempotency_key,
        )

    async def _emitir(
        self, tipo: str, *, venta_id: int, factura_id: int | None,
        total: Decimal, motivo: str | None, key: str,
    ) -> NotaLeer:
        existente = await self._repo.buscar_por_idempotency(key)
        if existente is not None:
            return existente  # idempotente: no re-emite ni duplica
        prefijo = self._config.prefix if self._config else None
        nota = await self._repo.crear_pendiente(
            tipo=tipo, venta_id=venta_id, factura_id=factura_id, motivo=motivo,
            prefijo=prefijo, idempotency_key=key,
        )
        res = await self._llamar_matias(tipo, factura_id=factura_id, total=total, motivo=motivo)
        await self._repo.registrar_evento(
            factura_id, evento=f"emision_{tipo}", estado=res.categoria, payload=res.raw or {}
        )
        if res.categoria == "aceptada" and res.cufe:
            return await self._repo.marcar_aceptada(nota.id, cufe=res.cufe, dian_respuesta=res.raw or {"cufe": res.cufe})
        if res.categoria == "rechazada":
            dian = {"rechazo": res.error_msg, **(res.raw or {})}
            return await self._repo.marcar_rechazada(nota.id, error_msg=res.error_msg or "rechazo MATIAS", dian_respuesta=dian)
        return await self._repo.marcar_error(nota.id, error_msg=res.error_msg or "error de emisión")

    async def _llamar_matias(
        self, tipo: str, *, factura_id: int | None, total: Decimal, motivo: str | None
    ) -> EmisionResultado:
        """Llama a MATIAS con un payload mínimo de nota; envuelve TODA la red (no propaga).

        Sin cliente MATIAS inyectado la nota queda `error` reintentable (nunca en pendiente eterna)."""
        if self._matias is None:
            return EmisionResultado(False, error_msg="sin cliente MATIAS", categoria="error")
        payload = {
            "tipo": tipo, "factura_id": factura_id, "total": str(total), "notes": motivo or "",
        }
        try:
            return await self._matias.emitir_factura(payload)
        except Exception:  # noqa: BLE001 — transporte/timeout: reintentable, no propaga al caller
            log.warning("emitir_nota_fallo_transporte", tipo=tipo, factura_id=factura_id, exc_info=True)
            return EmisionResultado(False, error_msg="fallo de transporte", categoria="error")


class SqlNotasRepository:
    """Acceso a datos de notas electrónicas: ÚNICO lugar con SQL (regla #2). Sesión del tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _cargar(self, nota_id: int) -> NotaElectronica:
        return (
            await self._s.execute(select(NotaElectronica).where(NotaElectronica.id == nota_id))
        ).scalar_one()

    async def buscar_por_idempotency(self, key: str) -> NotaLeer | None:
        orm = (
            await self._s.execute(
                select(NotaElectronica).where(NotaElectronica.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return NotaLeer.model_validate(orm) if orm is not None else None

    async def crear_pendiente(
        self, *, tipo: str, venta_id: int | None, factura_id: int | None,
        motivo: str | None, prefijo: str | None, idempotency_key: str,
    ) -> NotaLeer:
        orm = NotaElectronica(
            tipo=tipo, venta_id=venta_id, factura_id=factura_id, motivo=motivo,
            prefijo=prefijo, estado="pendiente", idempotency_key=idempotency_key,
        )
        self._s.add(orm)
        await self._s.flush()  # asigna id y dispara la UNIQUE de idempotency_key
        await publish(self._s, "nota_pendiente", {"id": orm.id, "tipo": tipo, "venta_id": venta_id})
        return NotaLeer.model_validate(orm)

    async def marcar_aceptada(self, nota_id: int, *, cufe: str, dian_respuesta: dict) -> NotaLeer:
        orm = await self._cargar(nota_id)
        orm.estado, orm.cufe, orm.emitido_en, orm.dian_respuesta = "aceptada", cufe, now_co(), dian_respuesta
        await self._s.flush()
        await publish(self._s, "nota_aceptada", {"id": orm.id, "cufe": cufe})
        return NotaLeer.model_validate(orm)

    async def marcar_rechazada(self, nota_id: int, *, error_msg: str, dian_respuesta: dict) -> NotaLeer:
        orm = await self._cargar(nota_id)
        orm.estado, orm.emitido_en, orm.dian_respuesta = "rechazada", now_co(), dian_respuesta
        await self._s.flush()
        await publish(self._s, "nota_rechazada", {"id": orm.id, "error": error_msg})
        return NotaLeer.model_validate(orm)

    async def marcar_error(self, nota_id: int, *, error_msg: str) -> NotaLeer:
        orm = await self._cargar(nota_id)
        orm.estado, orm.intentos, orm.dian_respuesta = "error", orm.intentos + 1, {"error": error_msg}
        await self._s.flush()
        await publish(self._s, "nota_error", {"id": orm.id, "error": error_msg})
        return NotaLeer.model_validate(orm)

    async def registrar_evento(
        self, factura_id: int | None, *, evento: str, estado: str, payload: dict
    ) -> None:
        """Bitácora del evento DIAN de la nota (envío/desenlace) en `eventos_dian`."""
        self._s.add(EventoDian(factura_id=factura_id, evento=evento, estado=estado, payload=payload))
        await self._s.flush()
