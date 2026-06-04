"""E3 RED — servicio de facturación (PURO: repo fake + MatiasClient fake; sin BD).

Pin del contrato: `crear_pendiente` reserva consecutivo (idempotente: no lo quema si la key existe);
`emitir` mapea venta→payload (E1), llama a MATIAS (E2) y persiste `aceptada`|`error`, idempotente si
ya está aceptada y tolerante a excepciones de transporte. En RED todos fallan por NotImplementedError.
"""
from datetime import datetime, timezone
from decimal import Decimal

from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.repository import (
    ClienteFiscalDatos,
    DatosVentaFiscal,
    FacturaLeer,
    ItemVentaDatos,
)
from modules.facturacion.service import ConfigFiscal, FacturacionService, _construir_factura_input

_CONFIG = ConfigFiscal(resolution_number="18760000001", prefix="FPR", notes="Punto Rojo", city_id_default="149")
_CUFE = "a" * 40

_DATOS = DatosVentaFiscal(
    cliente=ClienteFiscalDatos(
        tipo_id="CC", identificacion="123", dv=None, regimen_fiscal=None, nombre="Juan",
        email=None, mobile=None, address=None, municipio_dian="5001",
    ),
    items=[ItemVentaDatos(producto_id=5, descripcion="martillo", cantidad=Decimal("1"),
                          precio_unitario_con_iva=Decimal("11900"), pct_iva=Decimal("19"), unidad="Unidad")],
    metodo_pago="efectivo", es_fiado=False, fecha=datetime(2026, 6, 4, 10, 30, tzinfo=timezone.utc),
)


def _factura(*, id=1, estado="pendiente", consecutivo=7, cufe=None, intentos=0) -> FacturaLeer:
    return FacturaLeer(
        id=id, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=consecutivo,
        cufe=cufe, estado=estado, idempotency_key="k1", intentos=intentos,
    )


class _FakeRepo:
    """Repo fake en memoria que satisface `FacturacionRepo` (para GREEN; en RED no se ejecuta)."""

    def __init__(self, *, existente=None, factura=None):
        self._existente = existente
        self._facturas = {factura.id: factura} if factura else {}
        self.consecutivo_llamado = False
        self._next = 0

    async def buscar_por_idempotency(self, key):
        return self._existente

    async def siguiente_consecutivo(self):
        self.consecutivo_llamado = True
        self._next += 1
        return self._next

    async def crear_pendiente(self, *, venta_id, tipo, prefijo, consecutivo, idempotency_key):
        f = FacturaLeer(id=1, venta_id=venta_id, tipo=tipo, prefijo=prefijo, consecutivo=consecutivo,
                        cufe=None, estado="pendiente", idempotency_key=idempotency_key, intentos=0)
        self._facturas[f.id] = f
        return f

    async def obtener(self, factura_id):
        return self._facturas.get(factura_id)

    async def marcar_aceptada(self, factura_id, *, cufe, dian_respuesta):
        f = self._facturas[factura_id].model_copy(update={"estado": "aceptada", "cufe": cufe})
        self._facturas[factura_id] = f
        return f

    async def marcar_error(self, factura_id, *, error_msg):
        prev = self._facturas[factura_id]
        f = prev.model_copy(update={"estado": "error", "intentos": prev.intentos + 1})
        self._facturas[factura_id] = f
        return f

    async def datos_para_factura(self, venta_id):
        return _DATOS


class _FakeMatias:
    """MatiasClient fake: city_id canned y emitir_factura canned/excepción; registra si se llamó."""

    def __init__(self, *, resultado=None, excepcion=None, city="149"):
        self._resultado = resultado
        self._excepcion = excepcion
        self._city = city
        self.emitir_llamado = False

    async def city_id(self, dane_code):
        return self._city

    async def emitir_factura(self, payload):
        self.emitir_llamado = True
        if self._excepcion is not None:
            raise self._excepcion
        return self._resultado


def _svc(repo, matias):
    return FacturacionService(repo, matias, _CONFIG)


# --- crear_pendiente ---------------------------------------------------------

async def test_crear_pendiente_reserva_consecutivo():
    repo = _FakeRepo()
    res = await _svc(repo, _FakeMatias()).crear_pendiente(venta_id=10, idempotency_key="k1")
    assert res.estado == "pendiente"
    assert res.consecutivo == 1
    assert repo.consecutivo_llamado is True


async def test_crear_pendiente_idempotente():
    repo = _FakeRepo(existente=_factura(id=5))
    res = await _svc(repo, _FakeMatias()).crear_pendiente(venta_id=10, idempotency_key="k1")
    assert res.id == 5
    assert repo.consecutivo_llamado is False         # NO quema consecutivo


# --- emitir ------------------------------------------------------------------

async def test_emitir_exito():
    repo = _FakeRepo(factura=_factura())
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE))
    res = await _svc(repo, matias).emitir(1)
    assert res.estado == "aceptada" and res.cufe == _CUFE
    assert matias.emitir_llamado is True


async def test_emitir_rechazo():
    repo = _FakeRepo(factura=_factura())
    matias = _FakeMatias(resultado=EmisionResultado(ok=False, error_msg="Rechazado por DIAN"))
    res = await _svc(repo, matias).emitir(1)
    assert res.estado == "error" and res.intentos == 1


async def test_emitir_excepcion_transporte():
    repo = _FakeRepo(factura=_factura())
    matias = _FakeMatias(excepcion=RuntimeError("timeout"))
    res = await _svc(repo, matias).emitir(1)          # no propaga
    assert res.estado == "error"


async def test_emitir_idempotente_si_aceptada():
    repo = _FakeRepo(factura=_factura(estado="aceptada", cufe=_CUFE))
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe="b" * 40))
    res = await _svc(repo, matias).emitir(1)
    assert res.estado == "aceptada"
    assert matias.emitir_llamado is False             # no re-llama a MATIAS


# --- helper puro -------------------------------------------------------------

def test_construir_factura_input():
    fi = _construir_factura_input(_DATOS, _CONFIG, consecutivo=7, city_id_matias="149")
    assert fi.emision.document_number == "7"
    assert fi.emision.prefix == "FPR"
    assert fi.emision.resolution_number == "18760000001"
    assert fi.cliente.city_id_matias == "149"
    assert len(fi.items) == 1
