"""Servicio de facturación electrónica: orquesta E1 (payload UBL) + E2 (MatiasClient) + E3 (repo).

Split SIN cola (E4 mete ARQ/reintentos/reconciliador): `crear_pendiente` reserva el consecutivo y
crea la fila `pendiente` (idempotente por `idempotency_key`); `emitir(factura_id)` arma el payload,
llama a MATIAS y persiste el desenlace. Estados que escribe E3: SOLO `pendiente → aceptada | error`.

La config fiscal y la instancia de `MatiasClient` se INYECTAN (descifradas del control DB por la capa
de composición); el servicio NUNCA consulta secretos con SQL del tenant. SQL solo en `repository.py`.
"""
from dataclasses import dataclass
from typing import Protocol

from core.logging import get_logger
from modules.facturacion import ubl
from modules.facturacion.matias_client import MatiasClient
from modules.facturacion.repository import DatosVentaFiscal, FacturaLeer
from modules.facturacion.schemas import ClienteFiscal, DatosEmision, FacturaInput, ItemFactura

log = get_logger("facturacion.service")


@dataclass(frozen=True, slots=True)
class ConfigFiscal:
    """Parámetros DIAN de la empresa (inyectados; nunca leídos con SQL del tenant)."""

    resolution_number: str
    prefix: str
    notes: str
    city_id_default: str | None


class FacturacionRepo(Protocol):
    """Puerto de datos de facturación (lo implementa `SqlFacturacionRepository`; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> FacturaLeer | None: ...
    async def siguiente_consecutivo(self) -> int: ...
    async def crear_pendiente(
        self, *, venta_id: int | None, tipo: str, prefijo: str | None,
        consecutivo: int, idempotency_key: str,
    ) -> FacturaLeer: ...
    async def obtener(self, factura_id: int) -> FacturaLeer | None: ...
    async def marcar_aceptada(self, factura_id: int, *, cufe: str, dian_respuesta: dict) -> FacturaLeer: ...
    async def marcar_error(self, factura_id: int, *, error_msg: str) -> FacturaLeer: ...
    async def datos_para_factura(self, venta_id: int) -> DatosVentaFiscal | None: ...


def _construir_factura_input(
    datos: DatosVentaFiscal, config: ConfigFiscal, *, consecutivo: int, city_id_matias: str | None,
) -> FacturaInput:
    """PURO: mapea `DatosVentaFiscal` + `ConfigFiscal` a los schemas de E1 (`FacturaInput`).

    document_number=str(consecutivo); prefix/resolution/notes de `config`; fecha→date/hora→time;
    means_payment_id/payment_method_id desde `metodo_pago`/`es_fiado`; ClienteFiscal con
    `city_id_matias` o `config.city_id_default`; un `ItemFactura` por cada item.
    """
    emision = DatosEmision(
        resolution_number=config.resolution_number, prefix=config.prefix,
        document_number=str(consecutivo), fecha=datos.fecha.date(), hora=datos.fecha.time(),
        means_payment_id=ubl._MEDIOS_PAGO.get(datos.metodo_pago.lower(), 10),
        payment_method_id=2 if datos.es_fiado else 1, notes=config.notes,
    )
    c = datos.cliente
    cliente = ClienteFiscal(
        tipo_documento=c.tipo_id or "", numero=c.identificacion, dv=c.dv, nombre=c.nombre,
        regimen_fiscal=c.regimen_fiscal, email=c.email, mobile=c.mobile, address=c.address,
        municipio_dian=c.municipio_dian, city_id_matias=city_id_matias or config.city_id_default,
        city_name=None,
    )
    items = [
        ItemFactura(
            producto_id=it.producto_id, descripcion=it.descripcion, cantidad=it.cantidad,
            precio_unitario_con_iva=it.precio_unitario_con_iva, pct_iva=it.pct_iva, unidad=it.unidad,
        )
        for it in datos.items
    ]
    return FacturaInput(emision=emision, cliente=cliente, items=items)


class FacturacionService:
    """Crea el pendiente y emite la factura (sin cola; el worker/reintentos es E4)."""

    def __init__(self, repo: FacturacionRepo, matias: MatiasClient, config: ConfigFiscal) -> None:
        self._repo = repo
        self._matias = matias
        self._config = config

    async def crear_pendiente(self, venta_id: int, idempotency_key: str) -> FacturaLeer:
        """Reserva consecutivo y crea la fila `pendiente`; idempotente por `idempotency_key`."""
        existente = await self._repo.buscar_por_idempotency(idempotency_key)
        if existente is not None:
            return existente                          # idempotente: NO reserva consecutivo
        consecutivo = await self._repo.siguiente_consecutivo()
        return await self._repo.crear_pendiente(
            venta_id=venta_id, tipo="factura", prefijo=self._config.prefix,
            consecutivo=consecutivo, idempotency_key=idempotency_key,
        )

    async def emitir(self, factura_id: int) -> FacturaLeer:
        """Arma el payload, llama a MATIAS y persiste `aceptada`|`error` (idempotente si ya aceptada)."""
        f = await self._repo.obtener(factura_id)
        if f is None:
            return f                                  # caller maneja el None (no cubierto por tests)
        if f.estado == "aceptada":
            return f                                  # idempotente: sin tocar MATIAS
        datos = await self._repo.datos_para_factura(f.venta_id)
        if datos is None:
            return await self._repo.marcar_error(factura_id, error_msg="venta no encontrada")
        city = await self._matias.city_id(datos.cliente.municipio_dian)
        fi = _construir_factura_input(datos, self._config, consecutivo=f.consecutivo, city_id_matias=city)
        payload = ubl.armar_payload_factura(fi)
        try:
            res = await self._matias.emitir_factura(payload)
        except Exception as e:  # noqa: BLE001 — transporte/timeout → error persistido, no propaga (E4 reintenta)
            log.warning("emitir_fallo_transporte", exc_info=True)
            return await self._repo.marcar_error(factura_id, error_msg=str(e))
        if res.ok:
            return await self._repo.marcar_aceptada(factura_id, cufe=res.cufe, dian_respuesta={"cufe": res.cufe})
        return await self._repo.marcar_error(factura_id, error_msg=res.error_msg or "rechazo MATIAS")
