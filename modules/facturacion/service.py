"""Servicio de facturación electrónica: orquesta E1 (payload UBL) + E2 (MatiasClient) + E3 (repo).

Split SIN cola (E4 mete ARQ/reintentos/reconciliador): `crear_pendiente` reserva el consecutivo y
crea la fila `pendiente` (idempotente por `idempotency_key`); `emitir(factura_id)` arma el payload,
llama a MATIAS y persiste el desenlace. Estados que escribe E3: SOLO `pendiente → aceptada | error`.

La config fiscal y la instancia de `MatiasClient` se INYECTAN (descifradas del control DB por la capa
de composición); el servicio NUNCA consulta secretos con SQL del tenant. SQL solo en `repository.py`.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from core.logging import get_logger
from core.money import cuantizar
from modules.facturacion import ubl
from modules.facturacion.matias_client import MatiasClient, urls_documento
from modules.facturacion.politica import Decision, decidir_emision
from modules.facturacion.repository import DatosVentaFiscal, DocumentoFiscal, FacturaLeer
from modules.facturacion.schemas import (
    ClienteFiscal,
    DatosEmision,
    DatosEmisionPos,
    FacturaInput,
    ItemFactura,
    PosInput,
    PuntoVenta,
    SoftwareFabricante,
)

log = get_logger("facturacion.service")

# Tope de reintentos de emisión (política de plataforma, no per-empresa).
MAX_INTENTOS = 5


@dataclass(frozen=True, slots=True)
class ConfigFiscal:
    """Parámetros DIAN de la empresa (inyectados; nunca leídos con SQL del tenant).

    `ambiente` ('produccion'|'pruebas') es el ÚNICO ambiente DIAN de la empresa: lo comparten la emisión
    (Fase 6) y los eventos RADIAN (Slice 6b). Default seguro 'pruebas' (nunca emitir/enviar real sin
    declararlo a conciencia). La cuenta MATIAS define el ambiente efectivo; este valor lo declara la
    empresa y se muestra al operador en cada confirmación.
    """

    resolution_number: str
    prefix: str
    notes: str
    city_id_default: str | None
    ambiente: str = "pruebas"
    # POS electrónico (ADR 0012 D5): resolución/prefijo PROPIOS + datos fijos del punto de venta.
    # Todos None mientras la empresa no tenga POS configurado; `pos_completa` valida antes de emitir.
    resolution_pos: str | None = None
    prefix_pos: str | None = None
    pos_terminal: str | None = None
    pos_address: str | None = None
    pos_cashier_type: str | None = None
    # Identidad del software emisor (bloque `software_manufacturer`, exigido por el endpoint POS).
    pos_software_name: str | None = None
    pos_company_name: str | None = None
    pos_owner_name: str | None = None

    def pos_completa(self) -> bool:
        """True si están los parámetros mínimos para emitir POS. Fail-closed: faltar uno → "config
        incompleta" (error claro) en vez de un payload que MATIAS rechaza.

        Exige resolución y prefijo POS (`prefix_pos`: sin él el endpoint responde 404 al no poder
        desambiguar la resolución, ADR 0012 D4 corregido), los tres datos fijos del `point_of_sale` y
        los tres del `software_manufacturer` (sin ellos el endpoint responde 422)."""
        return all((
            self.resolution_pos, self.prefix_pos,
            self.pos_terminal, self.pos_address, self.pos_cashier_type,
            self.pos_software_name, self.pos_company_name, self.pos_owner_name,
        ))


class FacturacionRepo(Protocol):
    """Puerto de datos de facturación (lo implementa `SqlFacturacionRepository`; los tests lo falsean)."""

    async def buscar_por_idempotency(self, key: str) -> FacturaLeer | None: ...
    async def siguiente_consecutivo(self) -> int: ...
    async def crear_pendiente(
        self, *, venta_id: int | None, tipo: str, prefijo: str | None,
        consecutivo: int | None, idempotency_key: str,
    ) -> FacturaLeer: ...
    async def obtener(self, factura_id: int) -> FacturaLeer | None: ...
    async def marcar_aceptada(
        self, factura_id: int, *, cufe: str, dian_respuesta: dict,
        prefijo: str | None = None, consecutivo: int | None = None,
    ) -> FacturaLeer: ...
    async def marcar_rechazada(self, factura_id: int, *, error_msg: str, dian_respuesta: dict) -> FacturaLeer: ...
    async def marcar_error(self, factura_id: int, *, error_msg: str) -> FacturaLeer: ...
    async def datos_para_factura(self, venta_id: int) -> DatosVentaFiscal | None: ...
    async def existe_documento_para_venta(self, venta_id: int) -> bool: ...
    async def eliminar_pos_pendiente(self, venta_id: int) -> bool: ...
    async def documento_para_xml(self, factura_id: int) -> DocumentoFiscal | None: ...
    async def guardar_xml(
        self, factura_id: int, *, xml: str, xml_url: str | None, pdf_url: str | None
    ) -> None: ...
    async def buscar_por_cufe(self, cufe: str) -> FacturaLeer | None: ...
    async def buscar_por_numero(self, prefijo: str | None, consecutivo: int) -> FacturaLeer | None: ...
    async def anotar_anulacion(self, factura_id: int, *, dian_respuesta: dict) -> None: ...
    async def pendientes_para_reconciliar(
        self, *, antiguedad: datetime, limite: int
    ) -> list[FacturaLeer]: ...


@dataclass(frozen=True, slots=True)
class _ResEmision:
    """Desenlace de la llamada a MATIAS (FE o POS), normalizado para `_persistir`. Interno del servicio."""

    categoria: str
    error_msg: str | None = None
    cufe: str | None = None
    raw: dict | None = None
    numero: int | None = None
    prefijo: str | None = None


@dataclass(frozen=True, slots=True)
class ResumenReconciliacion:
    """Resultado de una corrida de reconciliación sobre un tenant (D7.2)."""

    revisadas: int = 0
    aceptadas: int = 0
    rechazadas: int = 0
    sin_cambio: int = 0
    ids_aceptadas: list[int] = field(default_factory=list)


def _cliente_e_items(
    datos: DatosVentaFiscal, config: ConfigFiscal, *, city_id_matias: str | None,
) -> tuple[ClienteFiscal, list[ItemFactura]]:
    """PURO: mapea el cliente fiscal + las líneas de la venta (compartido por FE y POS)."""
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
    return cliente, items


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
    cliente, items = _cliente_e_items(datos, config, city_id_matias=city_id_matias)
    return FacturaInput(emision=emision, cliente=cliente, items=items)


def _construir_pos_input(
    datos: DatosVentaFiscal, config: ConfigFiscal, *, city_id_matias: str | None,
) -> PosInput:
    """PURO: mapea `DatosVentaFiscal` + `ConfigFiscal` al `PosInput` (ADR 0012 D5). SIN número/prefijo.

    `point_of_sale`: cashier_name = vendedor de la venta; terminal/address/cashier_type = config POS;
    sales_code = consecutivo INTERNO de la venta; sub_total = total CON IVA (suma de las líneas)."""
    emision = DatosEmisionPos(
        resolution_number=config.resolution_pos, fecha=datos.fecha.date(), hora=datos.fecha.time(),
        means_payment_id=ubl._MEDIOS_PAGO.get(datos.metodo_pago.lower(), 10),
        payment_method_id=2 if datos.es_fiado else 1, notes=config.notes,
        prefix=config.prefix_pos,   # desambigua la resolución POS (ADR 0012 D4 corregido)
    )
    cliente, items = _cliente_e_items(datos, config, city_id_matias=city_id_matias)
    sub_total = cuantizar(sum((it.precio_unitario_con_iva * it.cantidad for it in datos.items), Decimal("0")))
    punto_venta = PuntoVenta(
        cashier_name=datos.vendedor_nombre or "CAJERO",
        terminal_number=config.pos_terminal, address=config.pos_address,
        cashier_type=config.pos_cashier_type, sales_code=str(datos.venta_consecutivo or ""),
        sub_total=sub_total,
    )
    software = SoftwareFabricante(
        owner_name=config.pos_owner_name, company_name=config.pos_company_name,
        software_name=config.pos_software_name,
    )
    return PosInput(
        emision=emision, cliente=cliente, items=items, punto_venta=punto_venta, software=software,
    )


class FacturacionService:
    """Crea el pendiente y emite la factura (sin cola; el worker/reintentos es E4)."""

    def __init__(
        self, repo: FacturacionRepo, matias: MatiasClient | None = None,
        config: ConfigFiscal | None = None,
    ) -> None:
        # `matias`/`config` solo hacen falta para `emitir` (worker); el endpoint arma el servicio
        # para `crear_pendiente` sin credenciales MATIAS (matias=None, config solo aporta `prefix`).
        self._repo = repo
        self._matias = matias
        self._config = config

    async def crear_pendiente(self, venta_id: int, idempotency_key: str) -> FacturaLeer:
        """Reserva consecutivo y crea la fila `pendiente` de FACTURA; idempotente por `idempotency_key`.

        Exclusión POS↔FE (ADR 0012 D1): al crear una FE NUEVA se suprime un POS aún `pendiente` de la
        misma venta (si el cliente pide factura, no se emite el POS). No toca un POS ya emitido."""
        existente = await self._repo.buscar_por_idempotency(idempotency_key)
        if existente is not None:
            return existente                          # idempotente: NO reserva consecutivo
        await self._repo.eliminar_pos_pendiente(venta_id)   # D1: la factura suprime el POS sin emitir
        consecutivo = await self._repo.siguiente_consecutivo()
        return await self._repo.crear_pendiente(
            venta_id=venta_id, tipo="factura", prefijo=self._config.prefix,
            consecutivo=consecutivo, idempotency_key=idempotency_key,
        )

    async def crear_pendiente_pos(self, venta_id: int) -> tuple[FacturaLeer | None, bool]:
        """Crea el pendiente tipo `pos` que cierra la venta de mostrador (ADR 0012 D2). Idempotente y excluyente.

        Devuelve `(factura, creada)`: el hook encola la emisión SOLO si `creada` (evita una segunda
        emisión —y un segundo documento DIAN— al reintentar la venta).
        - Idempotencia: clave fija `pos:{venta_id}` → `(existente, False)`.
        - Exclusión POS↔FE (D1): si la venta YA tiene un documento → `(None, False)`, no crea otro.
        - Número/prefijo NULL: los asigna MATIAS por autoincremento (D4), no se reserva secuencia local."""
        key = f"pos:{venta_id}"
        existente = await self._repo.buscar_por_idempotency(key)
        if existente is not None:
            return existente, False
        if await self._repo.existe_documento_para_venta(venta_id):
            return None, False                        # la venta ya tiene FE/POS: no se emite otro (D1)
        f = await self._repo.crear_pendiente(
            venta_id=venta_id, tipo="pos", prefijo=None, consecutivo=None, idempotency_key=key,
        )
        return f, True

    async def emitir(self, factura_id: int) -> Decision:
        """Emite el documento (FE o POS según `tipo`), persiste el estado de la política y devuelve la `Decision`.

        La fuente única del estado es `decidir_emision`; el `try` envuelve SOLO la llamada a MATIAS.
        """
        f = await self._repo.obtener(factura_id)
        if f is None:
            log.warning("emitir_documento_inexistente", factura_id=factura_id)
            return Decision("error", False, False)
        if f.estado == "aceptada":
            return Decision("aceptada", False, False)   # idempotente: sin tocar MATIAS
        datos = await self._repo.datos_para_factura(f.venta_id)
        if datos is None:
            await self._repo.marcar_error(factura_id, error_msg="venta no encontrada")
            return decidir_emision("error", intentos=f.intentos + 1, max_intentos=MAX_INTENTOS)

        if f.tipo == "pos" and not self._config.pos_completa():
            # Config POS incompleta: error CLARO, nunca un payload a medias (ADR 0012 D5).
            await self._repo.marcar_error(factura_id, error_msg="configuración POS incompleta")
            return decidir_emision("error", intentos=f.intentos + 1, max_intentos=MAX_INTENTOS)

        res = await self._llamar_matias(f, datos)
        decision = decidir_emision(res.categoria, intentos=f.intentos + 1, max_intentos=MAX_INTENTOS)
        await self._persistir(
            decision, factura_id, cufe=res.cufe, error_msg=res.error_msg, raw=res.raw,
            numero=res.numero, prefijo=res.prefijo,
        )
        return decision

    async def _llamar_matias(self, f: FacturaLeer, datos: DatosVentaFiscal) -> "_ResEmision":
        """Arma el payload (FE o POS) y llama a MATIAS; envuelve SOLO la red. No persiste."""
        city = await self._matias.city_id(datos.cliente.municipio_dian)
        try:
            if f.tipo == "pos":
                payload = ubl.armar_payload_pos(_construir_pos_input(datos, self._config, city_id_matias=city))
                res = await self._matias.emitir_pos(payload)
            else:
                fi = _construir_factura_input(datos, self._config, consecutivo=f.consecutivo, city_id_matias=city)
                res = await self._matias.emitir_factura(ubl.armar_payload_factura(fi))
        except Exception:  # noqa: BLE001 — transporte/timeout: la política decide reintento, no propaga
            log.warning("emitir_fallo_transporte", factura_id=f.id, exc_info=True)
            return _ResEmision("error", error_msg="fallo de transporte")
        # POS: el prefijo se persiste desde la config (fuente de verdad), NO del parser —el éxito real
        # no trae prefijo estructurado y el de MATIAS es solo diagnóstico (ADR 0012 D4 corregido).
        prefijo = self._config.prefix_pos if f.tipo == "pos" else res.prefijo
        return _ResEmision(res.categoria, error_msg=res.error_msg, cufe=res.cufe, raw=res.raw,
                           numero=res.numero, prefijo=prefijo)

    async def _persistir(
        self, decision: Decision, factura_id: int, *,
        cufe: str | None, error_msg: str | None, raw: dict | None,
        numero: int | None = None, prefijo: str | None = None,
    ) -> None:
        """Persiste el desenlace según `decision.estado` (la fuente del estado es `decidir_emision`).

        `raw` = respuesta MATIAS COMPLETA (histórico fiscal D7.3): se guarda íntegra en `dian_respuesta`.
        `numero`/`prefijo` (POS, D4) se persisten al aceptar. En rechazo se antepone la clave `rechazo`
        (de la que `_motivo` saca el texto legible) sin perder la respuesta cruda."""
        if decision.estado == "aceptada":
            await self._repo.marcar_aceptada(
                factura_id, cufe=cufe, dian_respuesta=raw or {"cufe": cufe},
                consecutivo=numero, prefijo=prefijo,   # POS: número/prefijo asignados por MATIAS (D4)
            )
        elif decision.estado == "rechazada":
            dian = {"rechazo": error_msg, **(raw or {})}
            await self._repo.marcar_rechazada(factura_id, error_msg=error_msg or "rechazo MATIAS", dian_respuesta=dian)
        else:
            await self._repo.marcar_error(factura_id, error_msg=error_msg or "error de emisión")

    async def descargar_documento(self, factura_id: int) -> bool:
        """Archiva el XML técnico de una factura aceptada (D7.3). Devuelve si NO hace falta reintentar.

        Idempotente y defensivo: si la factura no existe, no está aceptada, no tiene CUFE o ya tiene
        XML → no hay nada que hacer (True, sin tocar MATIAS). Solo un fallo de transporte devuelve
        False, que el worker traduce a `Retry` con el backoff existente. Las URLs salen de la respuesta
        MATIAS ya guardada (`urls_documento`), sin llamadas extra."""
        doc = await self._repo.documento_para_xml(factura_id)
        if doc is None or doc.estado != "aceptada" or doc.tiene_xml or not doc.cufe:
            return True
        try:
            xml = await self._matias.obtener_xml(doc.cufe)
        except Exception:  # noqa: BLE001 — transporte/HTTP: reintentar (no perder el archivado)
            log.warning("descargar_xml_fallo", factura_id=factura_id, exc_info=True)
            return False
        xml_url, pdf_url = urls_documento(doc.dian_respuesta)
        await self._repo.guardar_xml(factura_id, xml=xml, xml_url=xml_url, pdf_url=pdf_url)
        log.info("xml_archivado", factura_id=factura_id)
        return True

    async def aplicar_evento_dian(self, evento: str, payload: dict) -> tuple[str, int | None]:
        """Aplica un evento del webhook MATIAS (D7.1) a la factura correlacionada. Idempotente.

        Devuelve `(accion, factura_id)`: `accion` ∈ {aceptada, rechazada, anulada, sin_factura, ignorado}.
        Correlaciona por CUFE y, si el evento no lo trae, por prefijo+consecutivo. accepted/rejected/voided
        reusan `marcar_aceptada`/`marcar_rechazada`/`anotar_anulacion` (mismo estado + SSE que la emisión
        síncrona); cada transición es idempotente. El worker encola el archivado del XML si quedó aceptada."""
        cufe, prefijo, consecutivo = _datos_evento(payload)
        f = await self._repo.buscar_por_cufe(cufe) if cufe else None
        if f is None and consecutivo is not None:
            f = await self._repo.buscar_por_numero(prefijo, consecutivo)
        if f is None:
            log.warning("webhook_sin_factura", evento=evento)
            return "sin_factura", None

        e = evento.lower()
        if e.endswith("accepted"):
            if f.estado != "aceptada":
                await self._repo.marcar_aceptada(f.id, cufe=cufe or f.cufe, dian_respuesta=payload)
            return "aceptada", f.id
        if e.endswith("rejected"):
            if f.estado != "rechazada":
                dian = {"rechazo": _motivo_evento(payload), **payload}
                await self._repo.marcar_rechazada(f.id, error_msg=_motivo_evento(payload), dian_respuesta=dian)
            return "rechazada", f.id
        if e.endswith("voided"):
            if f.estado != "anulada":
                await self._repo.anotar_anulacion(f.id, dian_respuesta=payload)
            return "anulada", f.id
        log.info("webhook_evento_ignorado", evento=evento)
        return "ignorado", f.id


    async def reconciliar(self, *, antiguedad: datetime, limite: int) -> ResumenReconciliacion:
        """Barre las facturas estancadas y consulta su estado en MATIAS (red de respaldo del webhook, D7.2).

        Por cada `pendiente`/`error` vieja: consulta `/status`; si DIAN ya la validó → `marcar_aceptada`
        (solo con CUFE: sin él no se puede archivar); si la rechazó → `marcar_rechazada`; si sigue en
        proceso o la consulta falla → se deja igual. Devuelve conteos + los ids que pasaron a aceptada
        (el worker les encola el archivado del XML). Cierra el dead-letter silencioso."""
        facturas = await self._repo.pendientes_para_reconciliar(antiguedad=antiguedad, limite=limite)
        aceptadas = rechazadas = sin_cambio = 0
        ids_aceptadas: list[int] = []
        for f in facturas:
            if f.consecutivo is None:
                sin_cambio += 1
                continue
            try:
                est = await self._matias.consultar_estado(
                    prefijo=f.prefijo, consecutivo=f.consecutivo,
                    resolution=self._config.resolution_number if self._config else None,
                )
            except Exception:  # noqa: BLE001 — transporte: no se pudo consultar, se deja igual
                log.warning("reconciliar_consulta_fallo", factura_id=f.id, exc_info=True)
                sin_cambio += 1
                continue
            cufe = est.cufe or f.cufe
            if est.categoria == "aceptada" and cufe:
                await self._repo.marcar_aceptada(f.id, cufe=cufe, dian_respuesta=est.raw or {"cufe": cufe})
                aceptadas += 1
                ids_aceptadas.append(f.id)
            elif est.categoria == "rechazada":
                await self._repo.marcar_rechazada(
                    f.id, error_msg="rechazada (reconciliación)", dian_respuesta=est.raw or {"rechazo": "DIAN"}
                )
                rechazadas += 1
            else:
                sin_cambio += 1
        log.info("reconciliar_resumen", revisadas=len(facturas), aceptadas=aceptadas, rechazadas=rechazadas)
        return ResumenReconciliacion(len(facturas), aceptadas, rechazadas, sin_cambio, ids_aceptadas)


def _datos_evento(payload: dict) -> tuple[str | None, str | None, int | None]:
    """Extrae (cufe, prefijo, consecutivo) del payload del webhook. PURO; tolera claves ausentes.

    Acepta el dato anidado en `document`/`data` (varias formas del proveedor) o en la raíz. El número
    legal puede venir como `number`/`document_number` (str con prefijo embebido) → se separa a int."""
    cuerpo = payload.get("document") or payload.get("data") or payload
    cufe = (cuerpo.get("XmlDocumentKey") or cuerpo.get("document_key") or cuerpo.get("cufe")
            or cuerpo.get("cude") or "")
    cufe = str(cufe).strip() or None
    prefijo = cuerpo.get("prefix") or cuerpo.get("prefijo")
    consecutivo = _solo_digitos(cuerpo.get("number") or cuerpo.get("document_number") or cuerpo.get("consecutivo"))
    return cufe, (str(prefijo) if prefijo else None), consecutivo


def _solo_digitos(valor) -> int | None:
    """Consecutivo entero desde un número que puede traer prefijo embebido (FPR1024 → 1024). None si no hay."""
    if valor is None:
        return None
    digitos = "".join(c for c in str(valor) if c.isdigit())
    return int(digitos) if digitos else None


def _motivo_evento(payload: dict) -> str:
    """Texto legible del rechazo desde el payload del webhook (`message`/`reason`/`errors`)."""
    cuerpo = payload.get("document") or payload.get("data") or payload
    return str(cuerpo.get("message") or cuerpo.get("reason") or payload.get("message") or "rechazo DIAN")
