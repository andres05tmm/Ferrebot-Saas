"""Notas electrónicas crédito/débito (ADR 0026, Fase 3 Contable B + afinado del UBL).

Archivo NUEVO y autocontenido (no toca `service.py`/`repository.py`): persiste la nota en
`notas_electronicas`, la emite por el pipeline MATIAS existente (`emitir_nota` → `/notes/credit` |
`/notes/debit`) y deja la bitácora en `eventos_dian`. Idempotente por `idempotency_key`.

- **Nota crédito**: corrige a la BAJA una venta ya transmitida a DIAN (una devolución total/parcial). Es
  la vía OBLIGATORIA cuando la venta fue facturada: el borrado físico queda solo para ventas no
  transmitidas (lo bloquea el guard de ventas).
- **Nota débito**: ajusta al ALZA una venta facturada (mismo pipeline, `fe_tipo='nota_debito'`).

El UBL fino de la nota (§12 de `facturacion-matias-extract.md`) referencia la factura original
(`billing_reference`: número + CUFE + fecha) y el motivo DIAN (`discrepancy_response`), y reusa el
núcleo UBL de la FE (cliente, líneas con IVA incluido, `tax_totals`, `legal_monetary_totals`,
pre-check FAU04) para que la nota corrija la factura sobre las MISMAS bases. Las líneas se toman de la
venta original (nota de documento completo); la fidelidad de una nota de valor PARCIAL frente a una
devolución parcial queda como refinamiento posterior. El estado que persiste espeja la emisión de
facturas: pendiente → aceptada | rechazada | error, y un worker de reintentos barre las que quedan en
`error` (idempotente: nunca re-emite una nota ya aceptada).
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co, to_co
from core.events import publish
from core.logging import get_logger
from modules.facturacion import ubl
from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.models import EventoDian, FacturaElectronica, NotaElectronica
from modules.facturacion.repository import DatosVentaFiscal, SqlFacturacionRepository
from modules.facturacion.schemas import DatosEmisionNota, NotaInput, ReferenciaFactura
from modules.facturacion.service import MAX_INTENTOS, ConfigFiscal, _cliente_e_items

log = get_logger("facturacion.notas")

# Razón DIAN por defecto por tipo de nota (`discrepancy_response_id`): NC 1 = devolución parcial (el caso
# típico de una devolución); ND 4 = otros. El caller puede sobreescribirla (`razon_id`).
_RAZON_DEFAULT = {"nota_credito": 1, "nota_debito": 4}


def _razones(tipo: str) -> dict[int, str]:
    """Mapa de descripciones de razón para el tipo de nota (§4/§12)."""
    return ubl.RAZONES_NC if tipo == "nota_credito" else ubl.RAZONES_ND


def _razon_default(tipo: str) -> int:
    """Código de razón por defecto para el tipo de nota."""
    return _RAZON_DEFAULT.get(tipo, 5 if tipo == "nota_credito" else 4)


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


@dataclass(frozen=True, slots=True)
class DatosNotaFiscal:
    """Lo que el servicio necesita para armar el UBL de la nota: datos fiscales de la venta corregida
    (`DatosVentaFiscal`) + la referencia a la factura original (`billing_reference`)."""

    venta: DatosVentaFiscal
    referencia: ReferenciaFactura


@dataclass(frozen=True, slots=True)
class ResumenReintentoNotas:
    """Resultado de una corrida del worker de reintentos de notas en `error` (mirror de la FE)."""

    revisadas: int = 0
    aceptadas: int = 0
    rechazadas: int = 0
    sin_cambio: int = 0
    ids_aceptadas: list[int] = field(default_factory=list)


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
    async def datos_fiscales_nota(
        self, venta_id: int | None, factura_id: int | None
    ) -> DatosNotaFiscal | None: ...
    async def notas_pendientes_para_reintento(
        self, *, antiguedad: datetime, limite: int
    ) -> list[NotaLeer]: ...


def _construir_nota_input(
    tipo: str, datos: DatosNotaFiscal, config: ConfigFiscal, *,
    city_id_matias: str | None, motivo: str | None, razon_id: int,
) -> NotaInput:
    """PURO: mapea `DatosNotaFiscal` + `ConfigFiscal` al `NotaInput` de E1 (§12).

    Cliente e items salen del núcleo FE compartido (`_cliente_e_items`): la nota corrige la factura sobre
    las MISMAS bases. `descripcion_razon` = el motivo humano si viene, si no la descripción DIAN estándar
    de `razon_id`. fecha/hora en HORA COLOMBIA (`to_co`), igual que la FE."""
    cliente, items = _cliente_e_items(datos.venta, config, city_id_matias=city_id_matias)
    fecha_co = to_co(datos.venta.fecha)
    emision = DatosEmisionNota(
        resolution_number=config.resolution_number, prefix=config.prefix,
        fecha=fecha_co.date(), hora=fecha_co.time(),
        means_payment_id=ubl._MEDIOS_PAGO.get(datos.venta.metodo_pago.lower(), 10),
        payment_method_id=2 if datos.venta.es_fiado else 1, notes=config.notes,
    )
    descripcion = (motivo or "").strip() or _razones(tipo).get(razon_id, "")
    return NotaInput(
        tipo=tipo, emision=emision, cliente=cliente, items=items,
        referencia=datos.referencia, razon_id=razon_id, descripcion_razon=descripcion,
    )


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
        motivo: str | None, idempotency_key: str, razon_id: int | None = None,
    ) -> NotaLeer:
        """Nota crédito (corrección a la baja) sobre una venta ya transmitida a DIAN.

        `total` queda como dato de la operación (la caja/fiado lo usan aguas arriba); el VALOR del UBL
        sale de las líneas de la venta (documento completo). `razon_id`: código DIAN de la razón (default
        1 = devolución parcial)."""
        return await self._emitir(
            "nota_credito", venta_id=venta_id, factura_id=factura_id,
            motivo=motivo, key=idempotency_key, razon_id=razon_id,
        )

    async def emitir_nota_debito(
        self, *, venta_id: int, factura_id: int | None, total: Decimal,
        motivo: str | None, idempotency_key: str, razon_id: int | None = None,
    ) -> NotaLeer:
        """Nota débito (ajuste al alza) sobre una venta facturada (mismo pipeline). `razon_id` default 4."""
        return await self._emitir(
            "nota_debito", venta_id=venta_id, factura_id=factura_id,
            motivo=motivo, key=idempotency_key, razon_id=razon_id,
        )

    async def _emitir(
        self, tipo: str, *, venta_id: int, factura_id: int | None,
        motivo: str | None, key: str, razon_id: int | None,
    ) -> NotaLeer:
        existente = await self._repo.buscar_por_idempotency(key)
        if existente is not None:
            return existente  # idempotente: no re-emite ni duplica
        prefijo = self._config.prefix if self._config else None
        nota = await self._repo.crear_pendiente(
            tipo=tipo, venta_id=venta_id, factura_id=factura_id, motivo=motivo,
            prefijo=prefijo, idempotency_key=key,
        )
        res = await self._llamar_matias(
            tipo, venta_id=venta_id, factura_id=factura_id, motivo=motivo,
            razon_id=razon_id if razon_id is not None else _razon_default(tipo),
        )
        return await self._persistir(nota.id, tipo, factura_id, res)

    async def _persistir(
        self, nota_id: int, tipo: str, factura_id: int | None, res: EmisionResultado, *,
        evento: str | None = None,
    ) -> NotaLeer:
        """Registra el evento DIAN y persiste el desenlace (mismo mapeo que la FE). Devuelve la nota."""
        await self._repo.registrar_evento(
            factura_id, evento=evento or f"emision_{tipo}", estado=res.categoria, payload=res.raw or {}
        )
        if res.categoria == "aceptada" and res.cufe:
            return await self._repo.marcar_aceptada(
                nota_id, cufe=res.cufe, dian_respuesta=res.raw or {"cufe": res.cufe}
            )
        if res.categoria == "rechazada":
            dian = {"rechazo": res.error_msg, **(res.raw or {})}
            return await self._repo.marcar_rechazada(
                nota_id, error_msg=res.error_msg or "rechazo MATIAS", dian_respuesta=dian
            )
        return await self._repo.marcar_error(nota_id, error_msg=res.error_msg or "error de emisión")

    async def _llamar_matias(
        self, tipo: str, *, venta_id: int | None, factura_id: int | None,
        motivo: str | None, razon_id: int,
    ) -> EmisionResultado:
        """Arma el UBL fino de la nota (§12) y llama a MATIAS; envuelve TODA la red (no propaga).

        Sin cliente/config MATIAS, o sin datos de la venta/factura, la nota queda `error` reintentable
        (nunca en pendiente eterna). El `city_id` (DANE→MATIAS) también es red: un fallo ahí es
        transitorio y debe marcar error, no propagar (igual que la FE)."""
        if self._matias is None or self._config is None:
            return EmisionResultado(False, error_msg="sin cliente/config MATIAS", categoria="error")
        datos = await self._repo.datos_fiscales_nota(venta_id, factura_id)
        if datos is None:
            log.warning("nota_sin_datos_fiscales", tipo=tipo, venta_id=venta_id, factura_id=factura_id)
            return EmisionResultado(False, error_msg="datos de venta/factura no encontrados", categoria="error")
        try:
            city = await self._matias.city_id(datos.venta.cliente.municipio_dian)
            nota_input = _construir_nota_input(
                tipo, datos, self._config, city_id_matias=city, motivo=motivo, razon_id=razon_id
            )
            payload = ubl.armar_payload_nota(nota_input)
            return await self._matias.emitir_nota(tipo, payload)
        except Exception:  # noqa: BLE001 — transporte/timeout/FAU04: reintentable, no propaga al caller
            log.warning("emitir_nota_fallo_transporte", tipo=tipo, factura_id=factura_id, exc_info=True)
            return EmisionResultado(False, error_msg="fallo de transporte", categoria="error")

    async def reintentar_pendientes(
        self, *, antiguedad: datetime, limite: int
    ) -> ResumenReintentoNotas:
        """Worker de reintentos: re-emite las notas en `error`/`pendiente` estancadas (mirror de la FE).

        INVARIANTE (idempotencia, ADR 0026 §8): NUNCA re-emite una nota ya `aceptada` —el repo la filtra
        y aquí se re-chequea el estado antes de tocar MATIAS—, así un reintento no crea un segundo
        documento DIAN. `intentos` acota los reintentos (el repo excluye las que llegaron a `MAX_INTENTOS`:
        quedan en `error` terminal). Devuelve conteos + los ids que pasaron a aceptada."""
        notas = await self._repo.notas_pendientes_para_reintento(antiguedad=antiguedad, limite=limite)
        aceptadas = rechazadas = sin_cambio = 0
        ids_aceptadas: list[int] = []
        for n in notas:
            if n.estado == "aceptada":  # invariante: nunca re-emitir una nota ya aceptada
                sin_cambio += 1
                continue
            res = await self._llamar_matias(
                n.tipo, venta_id=n.venta_id, factura_id=n.factura_id,
                motivo=n.motivo, razon_id=_razon_default(n.tipo),
            )
            nota = await self._persistir(n.id, n.tipo, n.factura_id, res, evento=f"reintento_{n.tipo}")
            if res.categoria == "aceptada" and res.cufe:
                aceptadas += 1
                ids_aceptadas.append(nota.id)
            elif res.categoria == "rechazada":
                rechazadas += 1
            else:
                sin_cambio += 1
        log.info("reintentar_notas_resumen", revisadas=len(notas), aceptadas=aceptadas, rechazadas=rechazadas)
        return ResumenReintentoNotas(len(notas), aceptadas, rechazadas, sin_cambio, ids_aceptadas)


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

    async def datos_fiscales_nota(
        self, venta_id: int | None, factura_id: int | None
    ) -> DatosNotaFiscal | None:
        """Datos fiscales de la venta corregida + la referencia a la factura original (`billing_reference`).

        Requiere venta Y factura: sin una factura ACEPTADA (con CUFE) no hay a qué referir la nota → None
        (el servicio lo trata como `error` reintentable). Reusa `datos_para_factura` (mismo mapeo de
        cliente/líneas que la FE) para que la nota corrija sobre las mismas bases."""
        if venta_id is None or factura_id is None:
            return None
        datos_venta = await SqlFacturacionRepository(self._s).datos_para_factura(venta_id)
        if datos_venta is None:
            return None
        row = (
            await self._s.execute(
                select(
                    FacturaElectronica.prefijo, FacturaElectronica.consecutivo,
                    FacturaElectronica.cufe, FacturaElectronica.emitido_en, FacturaElectronica.creado_en,
                ).where(FacturaElectronica.id == factura_id)
            )
        ).one_or_none()
        if row is None or not row.cufe:
            return None
        numero = (
            f"{row.prefijo or ''}{row.consecutivo}" if row.consecutivo is not None else (row.prefijo or "")
        )
        fecha_ref = to_co(row.emitido_en or row.creado_en).date()
        return DatosNotaFiscal(
            venta=datos_venta,
            referencia=ReferenciaFactura(number=numero, cufe=row.cufe, fecha=fecha_ref),
        )

    async def notas_pendientes_para_reintento(
        self, *, antiguedad: datetime, limite: int
    ) -> list[NotaLeer]:
        """Notas `pendiente`/`error` creadas antes de `antiguedad` con `intentos < MAX_INTENTOS`.

        Excluye las que ya llegaron al tope (quedan en `error` terminal) y NUNCA las `aceptada`/`rechazada`
        (terminales). Las más viejas primero (id asc), acotado por `limite` (no saturar MATIAS por corrida)."""
        stmt = (
            select(NotaElectronica)
            .where(NotaElectronica.estado.in_(("pendiente", "error")))
            .where(NotaElectronica.creado_en < antiguedad)
            .where(NotaElectronica.intentos < MAX_INTENTOS)
            .order_by(NotaElectronica.id)
            .limit(limite)
        )
        filas = (await self._s.execute(stmt)).scalars().all()
        return [NotaLeer.model_validate(o) for o in filas]
