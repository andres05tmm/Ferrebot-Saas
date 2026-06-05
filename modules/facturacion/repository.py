"""Repositorio de facturación: ÚNICO lugar con SQL (regla no negociable #2).

Sesión del tenant (la base es la frontera; sin `empresa_id`). El consecutivo sale de
`fe_factura_consecutivo_seq`; las transiciones de estado emiten un evento `pg_notify`. Espejo de
`modules/ventas/repository.py` (`SqlVentasRepository`).
"""
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
    """Todo lo que el servicio necesita de la venta para armar el `FacturaInput` de E1."""

    cliente: ClienteFiscalDatos
    items: list[ItemVentaDatos]
    metodo_pago: str
    es_fiado: bool
    fecha: datetime


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
        consecutivo: int, idempotency_key: str,
    ) -> FacturaLeer:
        """INSERT estado=pendiente; flush asigna id (y dispara la UNIQUE); publica `factura_pendiente`."""
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

    async def marcar_aceptada(self, factura_id: int, *, cufe: str, dian_respuesta: dict) -> FacturaLeer:
        """estado=aceptada, guarda cufe/`emitido_en`/`dian_respuesta`; publica `factura_aceptada`."""
        orm = await self._cargar(factura_id)
        orm.estado, orm.cufe, orm.emitido_en, orm.dian_respuesta = "aceptada", cufe, now_co(), dian_respuesta
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

    async def datos_para_factura(self, venta_id: int) -> DatosVentaFiscal | None:
        """Lee venta + ventas_detalle (LEFT JOIN productos) + clientes (LEFT JOIN); mapea a DTOs, o None."""
        venta = (
            await self._s.execute(
                text("SELECT metodo_pago, fecha, cliente_id FROM ventas WHERE id=:v"), {"v": venta_id}
            )
        ).one_or_none()
        if venta is None:
            return None
        cliente = await self._cliente_fiscal(venta.cliente_id)
        items = await self._items_venta(venta_id)
        return DatosVentaFiscal(
            cliente=cliente, items=items, metodo_pago=venta.metodo_pago,
            es_fiado=venta.metodo_pago.lower() == "fiado", fecha=venta.fecha,
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
