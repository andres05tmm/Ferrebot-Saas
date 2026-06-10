"""F2.2 — POS electrónico (ADR 0012): payload UBL tipo 20, autoincremento y rama de emisión.

Todo PURO/aislado (fakes + MockTransport). El hook post-venta y la exclusión viven en
`test_facturacion_pos_hook.py`."""
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from modules.facturacion import ubl
from modules.facturacion.matias_client import (
    EmisionResultado,
    MatiasClient,
    MatiasCredenciales,
    _parsear_emision_pos,
)
from modules.facturacion.repository import ClienteFiscalDatos, DatosVentaFiscal, FacturaLeer, ItemVentaDatos
from modules.facturacion.schemas import (
    ClienteFiscal,
    DatosEmisionPos,
    ItemFactura,
    PosInput,
    PuntoVenta,
)
from modules.facturacion.service import ConfigFiscal, FacturacionService, _construir_pos_input

_CUFE = "a" * 40


def _config(*, pos=True) -> ConfigFiscal:
    extra = dict(
        resolution_pos="POS-RES-1", prefix_pos="POS", pos_terminal="CAJA-1",
        pos_address="CRA 1 # 2-3", pos_cashier_type="2",
    ) if pos else {}
    return ConfigFiscal(resolution_number="r", prefix="FPR", notes="PR", city_id_default="149", **extra)


_DATOS = DatosVentaFiscal(
    cliente=ClienteFiscalDatos(
        tipo_id=None, identificacion=None, dv=None, regimen_fiscal=None, nombre="",
        email=None, mobile=None, address=None, municipio_dian="5001",
    ),
    items=[ItemVentaDatos(producto_id=5, descripcion="martillo", cantidad=Decimal("2"),
                          precio_unitario_con_iva=Decimal("11900"), pct_iva=Decimal("19"), unidad="Unidad")],
    metodo_pago="efectivo", es_fiado=False, fecha=datetime(2026, 6, 9, 10, 30, tzinfo=timezone.utc),
    vendedor_nombre="Ana", venta_consecutivo=42,
)


# --- payload UBL del POS -----------------------------------------------------

def test_armar_payload_pos_estructura():
    pos = _construir_pos_input(_DATOS, _config(), city_id_matias="149")
    payload = ubl.armar_payload_pos(pos)
    assert payload["type_document_id"] == 20                  # id INTERNO MATIAS (ADR D4)
    assert "prefix" not in payload and "document_number" not in payload   # autoincremento
    assert payload["resolution_number"] == "POS-RES-1"
    pv = payload["point_of_sale"]
    assert pv["cashier_name"] == "Ana" and pv["terminal_number"] == "CAJA-1"
    assert pv["address"] == "CRA 1 # 2-3" and pv["cashier_type"] == "2"
    assert pv["sales_code"] == "42"
    assert pv["sub_total"] == Decimal("23800.00")            # 2 × 11900 (con IVA)
    # consumidor final sin identificar → documento genérico (ya resuelto en armar_customer)
    assert payload["customer"]["dni"] == "222222222222"


def test_construir_pos_input_es_posinput():
    pos = _construir_pos_input(_DATOS, _config(), city_id_matias=None)
    assert isinstance(pos, PosInput) and isinstance(pos.punto_venta, PuntoVenta)
    assert isinstance(pos.emision, DatosEmisionPos)


# --- parser del autoincremento -----------------------------------------------

def test_parsear_emision_pos_extrae_numero_prefijo():
    res = _parsear_emision_pos(
        {"success": True, "XmlDocumentKey": _CUFE, "number": "POS1024", "prefix": "POS"}
    )
    assert res.ok is True and res.categoria == "aceptada"
    assert res.numero == 1024 and res.prefijo == "POS"


def test_parsear_emision_pos_rechazo():
    res = _parsear_emision_pos({"success": False, "message": "POS inválido"})
    assert res.ok is False and res.categoria == "rechazada" and res.numero is None


async def test_emitir_pos_client_mocktransport():
    cred = MatiasCredenciales(email="b@e.co", password="x", base_url="https://m.test/api")
    paths: list[str] = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "T", "expires_in": 3600})
        if request.url.path.endswith("/auto-increment/pos-documents"):
            return httpx.Response(200, json={"success": True, "XmlDocumentKey": _CUFE,
                                             "number": "POS5", "prefix": "POS"})
        return httpx.Response(404, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=cred.base_url)
    res = await MatiasClient(cred, client=http).emitir_pos({"type_document_id": 20})
    assert res.ok and res.numero == 5 and res.prefijo == "POS"
    assert any(p.endswith("/auto-increment/pos-documents") for p in paths)


# --- rama POS de FacturacionService.emitir -----------------------------------

def _pos_factura(*, estado="pendiente") -> FacturaLeer:
    return FacturaLeer(id=1, venta_id=10, tipo="pos", prefijo=None, consecutivo=None,
                       cufe=None, estado=estado, idempotency_key="pos:10", intentos=0)


class _RepoPos:
    def __init__(self, factura):
        self._f = factura
        self.aceptada = None
        self.error = None

    async def obtener(self, factura_id):
        return self._f

    async def datos_para_factura(self, venta_id):
        return _DATOS

    async def marcar_aceptada(self, factura_id, *, cufe, dian_respuesta, prefijo=None, consecutivo=None):
        self.aceptada = {"cufe": cufe, "prefijo": prefijo, "consecutivo": consecutivo}

    async def marcar_error(self, factura_id, *, error_msg):
        self.error = error_msg


class _MatiasPos:
    def __init__(self, resultado):
        self._r = resultado
        self.emitir_pos_llamado = False

    async def city_id(self, dane):
        return "149"

    async def emitir_pos(self, payload):
        self.emitir_pos_llamado = True
        return self._r


async def test_emitir_pos_persiste_numero_y_prefijo():
    repo = _RepoPos(_pos_factura())
    matias = _MatiasPos(EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada",
                                         raw={"x": 1}, numero=1024, prefijo="POS"))
    d = await FacturacionService(repo, matias, _config()).emitir(1)
    assert d.estado == "aceptada" and matias.emitir_pos_llamado is True
    assert repo.aceptada == {"cufe": _CUFE, "prefijo": "POS", "consecutivo": 1024}


async def test_emitir_pos_config_incompleta_error_sin_red():
    repo = _RepoPos(_pos_factura())
    matias = _MatiasPos(None)
    d = await FacturacionService(repo, matias, _config(pos=False)).emitir(1)
    assert d.estado == "error" and matias.emitir_pos_llamado is False    # nunca arma payload a medias
    assert repo.error == "configuración POS incompleta"


# --- crear_pendiente_pos: idempotencia + exclusión ---------------------------

class _RepoCrear:
    def __init__(self, *, existente=None, existe_doc=False):
        self._existente = existente
        self._existe_doc = existe_doc
        self.creado = None

    async def buscar_por_idempotency(self, key):
        return self._existente

    async def existe_documento_para_venta(self, venta_id):
        return self._existe_doc

    async def crear_pendiente(self, *, venta_id, tipo, prefijo, consecutivo, idempotency_key):
        self.creado = {"venta_id": venta_id, "tipo": tipo, "prefijo": prefijo,
                       "consecutivo": consecutivo, "key": idempotency_key}
        return FacturaLeer(id=1, venta_id=venta_id, tipo=tipo, prefijo=prefijo, consecutivo=consecutivo,
                           cufe=None, estado="pendiente", idempotency_key=idempotency_key, intentos=0)


async def test_crear_pendiente_pos_crea_con_nulls():
    repo = _RepoCrear()
    f, creada = await FacturacionService(repo, None, _config()).crear_pendiente_pos(10)
    assert f is not None and creada is True and repo.creado["tipo"] == "pos"
    assert repo.creado["prefijo"] is None and repo.creado["consecutivo"] is None
    assert repo.creado["key"] == "pos:10"


async def test_crear_pendiente_pos_idempotente():
    ya = FacturaLeer(id=9, venta_id=10, tipo="pos", prefijo=None, consecutivo=None, cufe=None,
                     estado="pendiente", idempotency_key="pos:10", intentos=0)
    repo = _RepoCrear(existente=ya)
    f, creada = await FacturacionService(repo, None, _config()).crear_pendiente_pos(10)
    assert f.id == 9 and creada is False and repo.creado is None    # no crea otro, no re-encola


async def test_crear_pendiente_pos_excluido_si_ya_hay_documento():
    repo = _RepoCrear(existe_doc=True)
    f, creada = await FacturacionService(repo, None, _config()).crear_pendiente_pos(10)
    assert f is None and creada is False and repo.creado is None    # exclusión POS↔FE (D1)
