"""Repositorio de facturación: ÚNICO lugar con SQL (regla no negociable #2).

Sesión del tenant (la base es la frontera; sin `empresa_id`). El consecutivo sale de
`fe_factura_consecutivo_seq`; las transiciones de estado emiten un evento `pg_notify`. Espejo de
`modules/ventas/repository.py` (`SqlVentasRepository`).
"""
import json as _json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co, rango_dia_co
from core.events import publish
from modules.facturacion.models import FacturaElectronica


class FacturaLeer(BaseModel):
    """Vista de salida de una factura electrónica (mapea el ORM `FacturaElectronica`)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    venta_id: int | None
    tipo: str
    prefijo: str | None
    consecutivo: int | None
    cufe: str | None
    estado: str
    idempotency_key: str | None
    intentos: int
    # Fecha de creación (la trae el ORM; opcional para no romper construcciones manuales en tests).
    creado_en: datetime | None = None


class FacturaDetalle(FacturaLeer):
    """Detalle de una factura: la vista base + emisión, total de la venta ligada y motivo de rechazo."""

    emitido_en: datetime | None
    total: Decimal | None   # total de la venta ligada (None si la factura no tiene venta)
    motivo: str | None      # por qué se rechazó / falló (extraído de dian_respuesta), si aplica


class EstadoFiscalVenta(BaseModel):
    """Estado fiscal resumido de una venta para el badge del dashboard (lectura, sin secretos).

    Una venta tiene a lo sumo un documento (exclusión POS↔FE, ADR 0012 D1). `numero` (= consecutivo)
    y `prefijo` pueden venir None en un POS aún `pendiente`: MATIAS los asigna al aceptar (D4)."""

    tipo: str               # enum fe_tipo: 'pos' | 'factura' | 'nota_credito' | …
    estado: str             # enum fe_estado: pendiente | aceptada | rechazada | error | anulada
    cufe: str | None = None
    numero: int | None = None
    prefijo: str | None = None


def _prioridad_doc(orm: FacturaElectronica) -> tuple[int, int]:
    """Orden para elegir el documento representativo de una venta (mayor gana): no-anulado primero,
    luego el más reciente (`id` desc). Solo importa cuando un histórico dejó varios para una venta."""
    return (0 if orm.estado == "anulada" else 1, orm.id)


def _motivo(dian_respuesta: dict | None) -> str | None:
    """Extrae el motivo legible de `dian_respuesta` (rechazo/error). None si no hay o no aplica."""
    if not isinstance(dian_respuesta, dict):
        return None
    for clave in ("rechazo", "error", "mensaje", "message"):
        valor = dian_respuesta.get(clave)
        if valor:
            return valor if isinstance(valor, str) else str(valor)
    return None


@dataclass(frozen=True, slots=True)
class WebhookRecibido:
    """Un webhook de MATIAS ya registrado (idempotencia): evento + payload para que el worker lo aplique."""

    id: int
    webhook_id: str
    evento: str
    payload: dict


@dataclass(frozen=True, slots=True)
class DocumentoFiscal:
    """Lo que el job de archivado (D7.3) necesita de una factura para decidir si descarga el XML.

    `tiene_xml` ya resuelto (presencia de `xml_contenido`, sin traer el blob); `dian_respuesta` es la
    respuesta MATIAS completa de la que se extraen las URLs (`urls_documento`)."""

    estado: str
    cufe: str | None
    tiene_xml: bool
    dian_respuesta: dict | None


@dataclass(frozen=True, slots=True)
class ClienteFiscalDatos:
    """Datos fiscales CRUDOS del cliente (para construir el `ClienteFiscal` de E1).

    `tipo_id` = abreviatura del tipo de documento (CC/NIT/CE…); `identificacion` = documento sin dv.
    """

    tipo_id: str | None
    identificacion: str | None
    dv: str | None
    regimen_fiscal: str | None
    nombre: str
    email: str | None
    mobile: str | None
    address: str | None
    municipio_dian: str | None


@dataclass(frozen=True, slots=True)
class ItemVentaDatos:
    """Una línea de la venta, ya con el precio CON IVA y el % de IVA (para el `ItemFactura` de E1)."""

    producto_id: int | None
    descripcion: str
    cantidad: Decimal
    precio_unitario_con_iva: Decimal
    pct_iva: Decimal
    unidad: str


@dataclass(frozen=True, slots=True)
class DatosVentaFiscal:
    """Todo lo que el servicio necesita de la venta para armar el `FacturaInput`/`PosInput`.

    `vendedor_nombre` y `venta_consecutivo` solo los usa el POS (cashier_name + sales_code del
    `point_of_sale`); para la FE son irrelevantes."""

    cliente: ClienteFiscalDatos
    items: list[ItemVentaDatos]
    metodo_pago: str
    es_fiado: bool
    fecha: datetime
    vendedor_nombre: str = ""
    venta_consecutivo: int | None = None


class SqlFacturacionRepository:
    """Acceso a datos fiscales del tenant: consecutivo, estados y lectura de la venta a facturar."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _cargar(self, factura_id: int) -> FacturaElectronica:
        return (
            await self._s.execute(select(FacturaElectronica).where(FacturaElectronica.id == factura_id))
        ).scalar_one()

    async def buscar_por_idempotency(self, key: str) -> FacturaLeer | None:
        """Factura ya creada con esa `idempotency_key` (backstop de reintentos), o None."""
        orm = (
            await self._s.execute(
                select(FacturaElectronica).where(FacturaElectronica.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return FacturaLeer.model_validate(orm) if orm is not None else None

    async def siguiente_consecutivo(self) -> int:
        """Reserva el siguiente consecutivo de factura (`nextval('fe_factura_consecutivo_seq')`)."""
        return (
            await self._s.execute(text("SELECT nextval('fe_factura_consecutivo_seq')"))
        ).scalar_one()

    async def crear_pendiente(
        self, *, venta_id: int | None, tipo: str, prefijo: str | None,
        consecutivo: int | None, idempotency_key: str,
    ) -> FacturaLeer:
        """INSERT estado=pendiente; flush asigna id (y dispara la UNIQUE); publica `factura_pendiente`.

        `consecutivo`/`prefijo` pueden ir NULL para el POS (ADR 0012 D4): MATIAS los asigna por
        autoincremento y `marcar_aceptada` los persiste desde la respuesta."""
        orm = FacturaElectronica(
            venta_id=venta_id, tipo=tipo, prefijo=prefijo, consecutivo=consecutivo,
            idempotency_key=idempotency_key, estado="pendiente",
        )
        self._s.add(orm)
        await self._s.flush()  # asigna id y dispara la UNIQUE de idempotency_key
        await publish(self._s, "factura_pendiente", {"id": orm.id, "consecutivo": consecutivo})
        return FacturaLeer.model_validate(orm)

    async def obtener(self, factura_id: int) -> FacturaLeer | None:
        """Lee la factura por id, o None."""
        orm = (
            await self._s.execute(select(FacturaElectronica).where(FacturaElectronica.id == factura_id))
        ).scalar_one_or_none()
        return FacturaLeer.model_validate(orm) if orm is not None else None

    async def listar(
        self, *, desde: date | None = None, hasta: date | None = None, estado: str | None = None,
    ) -> list[FacturaLeer]:
        """Historial del rango (hora Colombia), opcionalmente filtrado por estado; más reciente primero."""
        stmt = select(FacturaElectronica)
        if desde is not None:
            inicio, _ = rango_dia_co(desde, desde)
            stmt = stmt.where(FacturaElectronica.creado_en >= inicio)
        if hasta is not None:
            _, fin = rango_dia_co(hasta, hasta)
            stmt = stmt.where(FacturaElectronica.creado_en <= fin)
        if estado is not None:
            stmt = stmt.where(FacturaElectronica.estado == estado)
        stmt = stmt.order_by(FacturaElectronica.id.desc())
        filas = (await self._s.execute(stmt)).scalars().all()
        return [FacturaLeer.model_validate(o) for o in filas]

    async def detalle(self, factura_id: int) -> FacturaDetalle | None:
        """Detalle de una factura: base + emisión + total de la venta ligada + motivo de rechazo/error."""
        orm = (
            await self._s.execute(select(FacturaElectronica).where(FacturaElectronica.id == factura_id))
        ).scalar_one_or_none()
        if orm is None:
            return None
        total = None
        if orm.venta_id is not None:
            total = (
                await self._s.execute(
                    text("SELECT total FROM ventas WHERE id=:v"), {"v": orm.venta_id}
                )
            ).scalar_one_or_none()
        base = FacturaLeer.model_validate(orm)
        return FacturaDetalle(
            **base.model_dump(),
            emitido_en=orm.emitido_en,
            total=Decimal(total) if total is not None else None,
            motivo=_motivo(orm.dian_respuesta),
        )

    async def marcar_aceptada(
        self, factura_id: int, *, cufe: str, dian_respuesta: dict,
        prefijo: str | None = None, consecutivo: int | None = None,
    ) -> FacturaLeer:
        """estado=aceptada, guarda cufe/`emitido_en`/`dian_respuesta`; publica `factura_aceptada`.

        `prefijo`/`consecutivo` (POS, ADR 0012 D4): cuando llegan asignados por MATIAS se persisten en la
        fila que nació con esos campos NULL; si son None se respeta lo que ya tuviera (FE)."""
        orm = await self._cargar(factura_id)
        orm.estado, orm.cufe, orm.emitido_en, orm.dian_respuesta = "aceptada", cufe, now_co(), dian_respuesta
        if prefijo is not None:
            orm.prefijo = prefijo
        if consecutivo is not None:
            orm.consecutivo = consecutivo
        await self._s.flush()
        await publish(self._s, "factura_aceptada", {"id": orm.id, "cufe": cufe})
        return FacturaLeer.model_validate(orm)

    async def marcar_rechazada(
        self, factura_id: int, *, error_msg: str, dian_respuesta: dict
    ) -> FacturaLeer:
        """estado=rechazada, `emitido_en`=now_co(), `dian_respuesta`; publica `factura_rechazada`.

        No incrementa `intentos`: es un terminal de negocio (la DIAN rechazó), no un fallo técnico.
        """
        orm = await self._cargar(factura_id)
        orm.estado, orm.emitido_en, orm.dian_respuesta = "rechazada", now_co(), dian_respuesta
        await self._s.flush()
        await publish(self._s, "factura_rechazada", {"id": orm.id, "error": error_msg})
        return FacturaLeer.model_validate(orm)

    async def marcar_error(self, factura_id: int, *, error_msg: str) -> FacturaLeer:
        """estado=error, intentos+1, `dian_respuesta={'error': error_msg}`; publica `factura_error`."""
        orm = await self._cargar(factura_id)
        orm.estado, orm.intentos, orm.dian_respuesta = "error", orm.intentos + 1, {"error": error_msg}
        await self._s.flush()
        await publish(self._s, "factura_error", {"id": orm.id, "error": error_msg})
        return FacturaLeer.model_validate(orm)

    async def existe_documento_para_venta(self, venta_id: int) -> bool:
        """True si la venta ya tiene un documento fiscal (FE o POS): exclusión POS↔FE (ADR 0012 D1)."""
        return (
            await self._s.execute(
                select(FacturaElectronica.id).where(FacturaElectronica.venta_id == venta_id).limit(1)
            )
        ).scalar_one_or_none() is not None

    async def estados_por_ventas(self, venta_ids: list[int]) -> dict[int, EstadoFiscalVenta]:
        """Estado fiscal de varias ventas en UNA sola query (sin N+1): `WHERE venta_id IN (...)`.

        A lo sumo un documento por venta (exclusión D1); si un histórico dejó varios para la misma venta
        elige el representativo con `_prioridad_doc` (no-anulado, luego el más reciente). Las ventas sin
        documento NO aparecen en el dict → el llamador las deja con `fiscal=None`. Lista vacía → `{}`
        sin tocar la BD."""
        if not venta_ids:
            return {}
        filas = (
            await self._s.execute(
                select(FacturaElectronica).where(FacturaElectronica.venta_id.in_(venta_ids))
            )
        ).scalars().all()
        elegido: dict[int, FacturaElectronica] = {}
        for orm in filas:
            vid = orm.venta_id
            if vid is None:
                continue
            actual = elegido.get(vid)
            if actual is None or _prioridad_doc(orm) > _prioridad_doc(actual):
                elegido[vid] = orm
        return {
            vid: EstadoFiscalVenta(
                tipo=orm.tipo, estado=orm.estado, cufe=orm.cufe,
                numero=orm.consecutivo, prefijo=orm.prefijo,
            )
            for vid, orm in elegido.items()
        }

    async def eliminar_pos_pendiente(self, venta_id: int) -> bool:
        """Elimina un POS aún `pendiente` de la venta (lo suprime cuando el cliente pide factura, D1).

        Solo borra `tipo='pos'` y `estado='pendiente'`: como el número POS lo asigna MATIAS por
        autoincremento (D4), un pendiente sin emitir no quemó consecutivo → borrarlo es limpio. NO toca
        un POS ya aceptado (ahí la corrección sería una nota). Devuelve si borró algo."""
        res = await self._s.execute(
            text("DELETE FROM facturas_electronicas WHERE venta_id=:v AND tipo='pos' AND estado='pendiente'"),
            {"v": venta_id},
        )
        await self._s.flush()
        return res.rowcount > 0

    async def pendientes_para_reconciliar(
        self, *, antiguedad: datetime, limite: int
    ) -> list[FacturaLeer]:
        """Facturas `pendiente`/`error` creadas antes de `antiguedad` (red de respaldo del webhook, D7.2).

        Las más viejas primero (id asc) y acotado por `limite` (no saturar MATIAS por tenant y corrida)."""
        stmt = (
            select(FacturaElectronica)
            .where(FacturaElectronica.estado.in_(("pendiente", "error")))
            .where(FacturaElectronica.creado_en < antiguedad)
            .order_by(FacturaElectronica.id)
            .limit(limite)
        )
        filas = (await self._s.execute(stmt)).scalars().all()
        return [FacturaLeer.model_validate(o) for o in filas]

    async def buscar_por_cufe(self, cufe: str) -> FacturaLeer | None:
        """Factura por su CUFE/CUDE (correlación de eventos del webhook MATIAS), o None."""
        orm = (
            await self._s.execute(select(FacturaElectronica).where(FacturaElectronica.cufe == cufe))
        ).scalar_one_or_none()
        return FacturaLeer.model_validate(orm) if orm is not None else None

    async def buscar_por_numero(self, prefijo: str | None, consecutivo: int) -> FacturaLeer | None:
        """Factura por prefijo+consecutivo (fallback del webhook cuando el evento no trae CUFE)."""
        stmt = select(FacturaElectronica).where(FacturaElectronica.consecutivo == consecutivo)
        stmt = stmt.where(FacturaElectronica.prefijo == prefijo) if prefijo else stmt
        orm = (await self._s.execute(stmt)).scalars().first()
        return FacturaLeer.model_validate(orm) if orm is not None else None

    async def anotar_anulacion(self, factura_id: int, *, dian_respuesta: dict) -> None:
        """Anula la factura (`document.voided`): estado=`anulada`, anota `dian_respuesta`, publica `factura_anulada`.

        `anulada` es un estado terminal (`aceptada → anulada`). Idempotente desde el servicio (no re-anota
        si ya está `anulada`); aquí el set es de por sí estable (re-anular deja todo en `anulada`)."""
        orm = await self._cargar(factura_id)
        orm.estado = "anulada"
        orm.dian_respuesta = {"anulada": True, **(dian_respuesta or {})}
        await self._s.flush()
        await publish(self._s, "factura_anulada", {"id": orm.id})

    async def registrar_recibido(self, webhook_id: str, evento: str, payload: dict) -> int | None:
        """Registra el webhook (idempotencia por `webhook_id` UNIQUE). Devuelve el id, o None si duplicado."""
        recibido_id = (
            await self._s.execute(
                text(
                    "INSERT INTO webhooks_matias_recibidos (webhook_id, evento, payload) "
                    "VALUES (:w, :e, CAST(:p AS jsonb)) ON CONFLICT (webhook_id) DO NOTHING RETURNING id"
                ),
                {"w": webhook_id, "e": evento, "p": _json.dumps(payload)},
            )
        ).scalar_one_or_none()
        return int(recibido_id) if recibido_id is not None else None

    async def leer_recibido(self, recibido_id: int) -> WebhookRecibido | None:
        """Lee un webhook registrado (evento + payload) para que el worker lo aplique, o None."""
        row = (
            await self._s.execute(
                text("SELECT id, webhook_id, evento, payload FROM webhooks_matias_recibidos WHERE id=:i"),
                {"i": recibido_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return WebhookRecibido(id=row.id, webhook_id=row.webhook_id, evento=row.evento, payload=row.payload)

    async def marcar_recibido_procesado(self, recibido_id: int) -> None:
        """Sella `procesado_en` del webhook (auditoría; el barrido de pendientes lo usará)."""
        await self._s.execute(
            text("UPDATE webhooks_matias_recibidos SET procesado_en=now() WHERE id=:i"),
            {"i": recibido_id},
        )
        await self._s.flush()

    async def documento_para_xml(self, factura_id: int) -> DocumentoFiscal | None:
        """Estado/cufe/dian_respuesta + si ya tiene XML archivado (sin traer el blob). None si no existe."""
        orm = (
            await self._s.execute(select(FacturaElectronica).where(FacturaElectronica.id == factura_id))
        ).scalar_one_or_none()
        if orm is None:
            return None
        return DocumentoFiscal(
            estado=orm.estado, cufe=orm.cufe,
            tiene_xml=orm.xml_contenido is not None, dian_respuesta=orm.dian_respuesta,
        )

    async def guardar_xml(
        self, factura_id: int, *, xml: str, xml_url: str | None, pdf_url: str | None
    ) -> None:
        """Archiva el XML técnico + las URLs MATIAS (D7.3). Idempotente desde el servicio (no re-descarga)."""
        orm = await self._cargar(factura_id)
        orm.xml_contenido, orm.xml_url, orm.pdf_url = xml, xml_url, pdf_url
        await self._s.flush()

    async def datos_para_factura(self, venta_id: int) -> DatosVentaFiscal | None:
        """Lee venta + ventas_detalle (LEFT JOIN productos) + clientes (LEFT JOIN); mapea a DTOs, o None."""
        venta = (
            await self._s.execute(
                text("SELECT v.metodo_pago, v.fecha, v.cliente_id, v.consecutivo, "
                     "COALESCE(u.nombre, '') AS vendedor_nombre "
                     "FROM ventas v LEFT JOIN usuarios u ON u.id = v.vendedor_id WHERE v.id=:v"),
                {"v": venta_id},
            )
        ).one_or_none()
        if venta is None:
            return None
        cliente = await self._cliente_fiscal(venta.cliente_id)
        items = await self._items_venta(venta_id)
        return DatosVentaFiscal(
            cliente=cliente, items=items, metodo_pago=venta.metodo_pago,
            es_fiado=venta.metodo_pago.lower() == "fiado", fecha=venta.fecha,
            vendedor_nombre=venta.vendedor_nombre, venta_consecutivo=venta.consecutivo,
        )

    async def _cliente_fiscal(self, cliente_id: int | None) -> ClienteFiscalDatos:
        """Datos fiscales del cliente; si la venta no tiene cliente → consumidor final (campos None)."""
        if cliente_id is None:
            return ClienteFiscalDatos(None, None, None, None, "", None, None, None, None)
        c = (
            await self._s.execute(
                text("SELECT nombre, tipo_documento, documento, correo, telefono, direccion, "
                     "ciudad_dane, regimen FROM clientes WHERE id=:c"),
                {"c": cliente_id},
            )
        ).one()
        return ClienteFiscalDatos(
            tipo_id=c.tipo_documento, identificacion=c.documento, dv=None, regimen_fiscal=c.regimen,
            nombre=c.nombre, email=c.correo, mobile=c.telefono, address=c.direccion,
            municipio_dian=c.ciudad_dane,
        )

    async def _items_venta(self, venta_id: int) -> list[ItemVentaDatos]:
        """Líneas de la venta con la unidad del producto (default 'Unidad' para varias/sin producto)."""
        filas = (
            await self._s.execute(
                text("SELECT d.producto_id, d.descripcion, d.cantidad, d.precio_unitario, d.iva, "
                     "p.unidad_medida FROM ventas_detalle d LEFT JOIN productos p ON p.id=d.producto_id "
                     "WHERE d.venta_id=:v ORDER BY d.id"),
                {"v": venta_id},
            )
        ).all()
        return [
            ItemVentaDatos(
                producto_id=f.producto_id, descripcion=f.descripcion or "",
                cantidad=f.cantidad, precio_unitario_con_iva=f.precio_unitario,
                pct_iva=Decimal(f.iva), unidad=f.unidad_medida or "Unidad",
            )
            for f in filas
        ]
