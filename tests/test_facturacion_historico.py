"""F2.1.1 — histórico fiscal: respuesta DIAN completa + archivado del XML (D7.3 del ADR 0012).

Tres planos, todos sin red:
- `urls_documento` / `_parsear_emision.raw`: parsers PUROS del shape MATIAS.
- `FacturacionService.descargar_documento`: orquestación con fakes (idempotente, solo aceptadas,
  reintenta solo en fallo de transporte).
- El job `descargar_documento` del worker y el disparo desde `emitir_documento` viven en
  `test_worker_jobs.py`; la persistencia real (xml_contenido) en `test_facturacion_repository_integration.py`.
"""
from datetime import datetime, timezone
from decimal import Decimal

from modules.facturacion.matias_client import EmisionResultado, _parsear_emision, urls_documento
from modules.facturacion.repository import (
    ClienteFiscalDatos,
    DatosVentaFiscal,
    DocumentoFiscal,
    FacturaLeer,
    ItemVentaDatos,
)
from modules.facturacion.service import ConfigFiscal, FacturacionService

_CONFIG = ConfigFiscal(resolution_number="18760000001", prefix="FPR", notes="PR", city_id_default="149")
_CUFE = "a" * 40


# --- parsers puros -----------------------------------------------------------

def test_parsear_emision_conserva_raw():
    data = {"success": True, "XmlDocumentKey": _CUFE, "urlinvoicexml": "http://m/x.xml"}
    res = _parsear_emision(data)
    assert res.ok is True
    assert res.raw == data                      # respuesta COMPLETA, no solo el cufe


def test_urls_documento_variantes():
    # claves típicas de MATIAS (urlinvoicexml/urlinvoicepdf) y alias defensivos.
    assert urls_documento({"urlinvoicexml": "x", "urlinvoicepdf": "p"}) == ("x", "p")
    assert urls_documento({"url_xml": "x", "url_pdf": "p"}) == ("x", "p")
    assert urls_documento({"xml_url": "x", "pdf_url": "p"}) == ("x", "p")
    assert urls_documento({}) == (None, None)
    assert urls_documento(None) == (None, None)
    assert urls_documento({"urlinvoicepdf": "p"}) == (None, "p")


# --- servicio: descargar_documento -------------------------------------------

class _RepoXml:
    """Repo fake: devuelve un `DocumentoFiscal` canned y registra el guardado del XML."""

    def __init__(self, doc: DocumentoFiscal | None) -> None:
        self._doc = doc
        self.guardado: dict | None = None

    async def documento_para_xml(self, factura_id: int) -> DocumentoFiscal | None:
        return self._doc

    async def guardar_xml(self, factura_id, *, xml, xml_url, pdf_url):
        self.guardado = {"id": factura_id, "xml": xml, "xml_url": xml_url, "pdf_url": pdf_url}


class _MatiasXml:
    """MATIAS fake: `obtener_xml` canned o excepción; registra si se llamó."""

    def __init__(self, *, xml="<Invoice/>", excepcion=None) -> None:
        self._xml = xml
        self._excepcion = excepcion
        self.llamado = False

    async def obtener_xml(self, track_id):
        self.llamado = True
        if self._excepcion is not None:
            raise self._excepcion
        return self._xml


def _doc(*, estado="aceptada", cufe=_CUFE, tiene_xml=False, dian=None) -> DocumentoFiscal:
    return DocumentoFiscal(
        estado=estado, cufe=cufe, tiene_xml=tiene_xml,
        dian_respuesta=dian if dian is not None else {"urlinvoicexml": "http://m/x", "urlinvoicepdf": "http://m/p"},
    )


def _svc(repo, matias):
    return FacturacionService(repo, matias, _CONFIG)


async def test_descargar_guarda_xml_y_urls():
    repo, matias = _RepoXml(_doc()), _MatiasXml(xml="<Invoice>ok</Invoice>")
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is True and matias.llamado is True
    assert repo.guardado == {
        "id": 1, "xml": "<Invoice>ok</Invoice>",
        "xml_url": "http://m/x", "pdf_url": "http://m/p",
    }


async def test_descargar_idempotente_si_ya_tiene_xml():
    repo, matias = _RepoXml(_doc(tiene_xml=True)), _MatiasXml()
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is True and matias.llamado is False and repo.guardado is None


async def test_descargar_no_aceptada_no_descarga():
    repo, matias = _RepoXml(_doc(estado="pendiente")), _MatiasXml()
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is True and matias.llamado is False and repo.guardado is None


async def test_descargar_inexistente_no_reintenta():
    repo, matias = _RepoXml(None), _MatiasXml()
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is True and matias.llamado is False


async def test_descargar_fallo_transporte_reintenta():
    repo, matias = _RepoXml(_doc()), _MatiasXml(excepcion=RuntimeError("timeout"))
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is False and repo.guardado is None   # False → el worker reintenta


async def test_descargar_sin_cufe_no_descarga():
    repo, matias = _RepoXml(_doc(cufe=None)), _MatiasXml()
    ok = await _svc(repo, matias).descargar_documento(1)
    assert ok is True and matias.llamado is False


# --- servicio: emitir persiste la respuesta DIAN completa --------------------

_DATOS = DatosVentaFiscal(
    cliente=ClienteFiscalDatos(
        tipo_id="CC", identificacion="123", dv=None, regimen_fiscal=None, nombre="Juan",
        email=None, mobile=None, address=None, municipio_dian="5001",
    ),
    items=[ItemVentaDatos(producto_id=5, descripcion="m", cantidad=Decimal("1"),
                          precio_unitario_con_iva=Decimal("11900"), pct_iva=Decimal("19"), unidad="Unidad")],
    metodo_pago="efectivo", es_fiado=False, fecha=datetime(2026, 6, 4, 10, 30, tzinfo=timezone.utc),
)


class _RepoEmitir:
    def __init__(self, factura):
        self._f = factura
        self.dian_respuesta = None

    async def obtener(self, factura_id):
        return self._f

    async def datos_para_factura(self, venta_id):
        return _DATOS

    async def marcar_aceptada(self, factura_id, *, cufe, dian_respuesta, prefijo=None, consecutivo=None):
        self.dian_respuesta = dian_respuesta
        return self._f.model_copy(update={"estado": "aceptada", "cufe": cufe})


class _MatiasEmitir:
    def __init__(self, raw):
        self._raw = raw

    async def city_id(self, dane):
        return "149"

    async def emitir_factura(self, payload):
        return EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada", raw=self._raw)


async def test_emitir_persiste_respuesta_dian_completa():
    f = FacturaLeer(id=1, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=7,
                    cufe=None, estado="pendiente", idempotency_key="k1", intentos=0)
    raw = {"success": True, "XmlDocumentKey": _CUFE, "urlinvoicexml": "http://m/x", "trackId": "T"}
    repo = _RepoEmitir(f)
    await FacturacionService(repo, _MatiasEmitir(raw), _CONFIG).emitir(1)
    assert repo.dian_respuesta == raw          # se guarda la respuesta COMPLETA, no {"cufe": ...}
