"""Notas crédito/débito (ADR 0026, servicio + UBL fino + worker de reintentos).

Cubre el servicio PURO (fakes, sin BD): idempotencia, desenlaces aceptada/rechazada/error, la bitácora
en `eventos_dian`, el UBL fino (§12: billing_reference + discrepancy_response + líneas FE) y el worker de
reintentos (idempotente: nunca re-emite una nota ya aceptada); y una integración contra base efímera real
que ata la devolución de una venta FACTURADA a su nota crédito. Los tests NUNCA llaman al MATIAS real
(usan los fakes existentes).
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.schemas import DevolucionCrear
from modules.devoluciones.service import DevolucionesService
from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.notas import (
    DatosNotaFiscal,
    NotaLeer,
    NotasService,
    SqlNotasRepository,
)
from modules.facturacion.repository import (
    ClienteFiscalDatos,
    DatosVentaFiscal,
    ItemVentaDatos,
)
from modules.facturacion.schemas import ReferenciaFactura
from modules.facturacion.service import ConfigFiscal
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService

_CONFIG = ConfigFiscal(resolution_number="18760000001", prefix="FPR", notes="PR", city_id_default="149")
_CUFE = "c" * 40
_CUFE_FACT = "f" * 40


def _datos_nota() -> DatosNotaFiscal:
    """Fixture de datos fiscales: venta de una línea con IVA + referencia a la factura original."""
    cliente = ClienteFiscalDatos(
        tipo_id="CC", identificacion="123", dv=None, regimen_fiscal="no_responsable",
        nombre="Juan Perez", email=None, mobile="3001112233", address="Cra 1", municipio_dian="149",
    )
    items = [
        ItemVentaDatos(
            producto_id=5, descripcion="Cemento", cantidad=Decimal("3"),
            precio_unitario_con_iva=Decimal("20000"), pct_iva=Decimal("19"), unidad="unidad",
        )
    ]
    venta = DatosVentaFiscal(
        cliente=cliente, items=items, metodo_pago="efectivo", es_fiado=False,
        fecha=datetime(2026, 7, 1, 20, 35, 47, tzinfo=timezone.utc),
    )
    referencia = ReferenciaFactura(number="FPR1024", cufe=_CUFE_FACT, fecha=datetime(2026, 7, 1).date())
    return DatosNotaFiscal(venta=venta, referencia=referencia)


class _FakeNotasRepo:
    """Repo fake en memoria que satisface `NotasRepo`."""

    def __init__(self, *, existente=None, datos=None):
        self._existente = existente
        self._datos = datos if datos is not None else _datos_nota()
        self._pendientes: list[NotaLeer] = []
        self._notas: dict[int, NotaLeer] = {}
        self.eventos: list[dict] = []
        self._next = 0

    async def buscar_por_idempotency(self, key):
        return self._existente

    async def crear_pendiente(self, *, tipo, venta_id, factura_id, motivo, prefijo, idempotency_key):
        self._next += 1
        n = NotaLeer(
            id=self._next, factura_id=factura_id, venta_id=venta_id, tipo=tipo, motivo=motivo,
            prefijo=prefijo, consecutivo=None, cufe=None, estado="pendiente",
            idempotency_key=idempotency_key, intentos=0,
        )
        self._notas[n.id] = n
        return n

    async def marcar_aceptada(self, nota_id, *, cufe, dian_respuesta):
        n = self._notas[nota_id].model_copy(update={"estado": "aceptada", "cufe": cufe})
        self._notas[nota_id] = n
        return n

    async def marcar_rechazada(self, nota_id, *, error_msg, dian_respuesta):
        n = self._notas[nota_id].model_copy(update={"estado": "rechazada"})
        self._notas[nota_id] = n
        return n

    async def marcar_error(self, nota_id, *, error_msg):
        prev = self._notas[nota_id]
        n = prev.model_copy(update={"estado": "error", "intentos": prev.intentos + 1})
        self._notas[nota_id] = n
        return n

    async def registrar_evento(self, factura_id, *, evento, estado, payload):
        self.eventos.append({"factura_id": factura_id, "evento": evento, "estado": estado})

    async def datos_fiscales_nota(self, venta_id, factura_id):
        if venta_id is None or factura_id is None:
            return None
        return self._datos

    async def notas_pendientes_para_reintento(self, *, antiguedad, limite):
        # Respeta el estado actual: una nota ya aceptada deja de listarse (idempotencia del reintento).
        vivas = [self._notas[n.id] for n in self._pendientes if self._notas[n.id].estado in ("pendiente", "error")]
        return vivas[:limite]

    # Helper de test: registra una nota "existente" (para el worker de reintentos).
    def sembrar(self, nota: NotaLeer) -> None:
        self._notas[nota.id] = nota
        self._pendientes.append(nota)
        self._next = max(self._next, nota.id)


class _FakeMatias:
    """MatiasClient fake: `emitir_nota` canned/excepción + `city_id`; captura el payload y el endpoint."""

    def __init__(self, *, resultado=None, excepcion=None):
        self._resultado = resultado
        self._excepcion = excepcion
        self.llamado = False
        self.llamadas = 0
        self.tipo = None
        self.payload = None

    async def city_id(self, dane):
        return "149" if dane else None

    async def emitir_nota(self, tipo, payload):
        self.llamado = True
        self.llamadas += 1
        self.tipo = tipo
        self.payload = payload
        if self._excepcion is not None:
            raise self._excepcion
        return self._resultado


def _svc(repo, matias):
    return NotasService(repo, matias, _CONFIG)


# --- servicio puro -----------------------------------------------------------
async def test_nota_credito_aceptada_registra_evento():
    repo = _FakeNotasRepo()
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada", raw={"ok": 1}))
    nota = await _svc(repo, matias).emitir_nota_credito(
        venta_id=10, factura_id=3, total=Decimal("60000"), motivo="devolución", idempotency_key="nc-1"
    )
    assert nota.estado == "aceptada" and nota.cufe == _CUFE and nota.tipo == "nota_credito"
    assert matias.llamado is True
    assert repo.eventos and repo.eventos[0]["evento"] == "emision_nota_credito"
    assert repo.eventos[0]["estado"] == "aceptada"


async def test_nota_credito_arma_ubl_con_billing_reference():
    """El UBL fino (§12): endpoint por tipo + billing_reference (CUFE de la factura) + discrepancy +
    líneas FE con tax_id='1'. La nota corrige la factura sobre las mismas bases (FAU04)."""
    repo = _FakeNotasRepo()
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada"))
    await _svc(repo, matias).emitir_nota_credito(
        venta_id=10, factura_id=3, total=Decimal("60000"), motivo="defectuoso", idempotency_key="nc-9"
    )
    assert matias.tipo == "nota_credito"
    p = matias.payload
    assert p["type_document_id"] == 5                       # NC
    assert p["billing_reference"]["uuid"] == _CUFE_FACT     # CUFE de la factura original
    assert p["billing_reference"]["number"] == "FPR1024"
    assert p["discrepancy_response"]["discrepancy_response_id"] == 1   # devolución parcial (default NC)
    assert p["discrepancy_response"]["description"] == "defectuoso"    # el motivo humano
    assert p["lines"] and p["lines"][0]["tax_totals"][0]["tax_id"] == "1"
    assert p["prefix"] == "FPR"
    assert "document_number" not in p                       # el consecutivo lo asigna MATIAS
    # payable_amount = total con IVA de la única línea (3 × 20000).
    assert p["legal_monetary_totals"]["payable_amount"] == Decimal("60000.00")


async def test_nota_debito_usa_type_document_4():
    repo = _FakeNotasRepo()
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada"))
    await _svc(repo, matias).emitir_nota_debito(
        venta_id=10, factura_id=3, total=Decimal("5000"), motivo=None, idempotency_key="nd-9"
    )
    assert matias.tipo == "nota_debito"
    assert matias.payload["type_document_id"] == 4
    # Sin motivo humano, la descripción es la razón DIAN estándar (ND default 4 = Otros).
    assert matias.payload["discrepancy_response"]["description"] == "Otros"


async def test_nota_idempotente_no_reemite():
    existente = NotaLeer(
        id=7, factura_id=3, venta_id=10, tipo="nota_credito", motivo=None, prefijo="FPR",
        consecutivo=None, cufe=_CUFE, estado="aceptada", idempotency_key="nc-1", intentos=0,
    )
    repo = _FakeNotasRepo(existente=existente)
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe="z" * 40, categoria="aceptada"))
    nota = await _svc(repo, matias).emitir_nota_credito(
        venta_id=10, factura_id=3, total=Decimal("60000"), motivo=None, idempotency_key="nc-1"
    )
    assert nota.id == 7
    assert matias.llamado is False   # no re-llama a MATIAS


async def test_nota_rechazada():
    repo = _FakeNotasRepo()
    matias = _FakeMatias(resultado=EmisionResultado(ok=False, error_msg="rechazo DIAN", categoria="rechazada"))
    nota = await _svc(repo, matias).emitir_nota_debito(
        venta_id=10, factura_id=3, total=Decimal("5000"), motivo="ajuste", idempotency_key="nd-1"
    )
    assert nota.estado == "rechazada" and nota.tipo == "nota_debito"


async def test_nota_error_transporte_no_propaga():
    repo = _FakeNotasRepo()
    matias = _FakeMatias(excepcion=RuntimeError("timeout"))
    nota = await _svc(repo, matias).emitir_nota_credito(
        venta_id=10, factura_id=3, total=Decimal("60000"), motivo=None, idempotency_key="nc-2"
    )
    assert nota.estado == "error" and nota.intentos == 1


async def test_nota_sin_datos_fiscales_queda_error():
    """Sin factura referenciable (datos None) la nota queda `error` reintentable, sin tocar MATIAS."""
    repo = _FakeNotasRepo()
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada"))
    nota = await _svc(repo, matias).emitir_nota_credito(
        venta_id=10, factura_id=None, total=Decimal("60000"), motivo=None, idempotency_key="nc-3"
    )
    assert nota.estado == "error" and matias.llamado is False


# --- worker de reintentos (idempotencia: nunca re-emite una aceptada) --------
async def test_reintento_emite_nota_en_error_y_no_la_reemite():
    """Invariante de idempotencia: un `error` se re-emite y pasa a `aceptada`; una segunda corrida NO la
    vuelve a emitir (el repo ya no la lista como pendiente)."""
    nota_err = NotaLeer(
        id=1, factura_id=3, venta_id=10, tipo="nota_credito", motivo="defectuoso", prefijo="FPR",
        consecutivo=None, cufe=None, estado="error", idempotency_key="nc-r1", intentos=1,
    )
    repo = _FakeNotasRepo()
    repo.sembrar(nota_err)
    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada", raw={"ok": 1}))
    svc = _svc(repo, matias)

    resumen1 = await svc.reintentar_pendientes(antiguedad=datetime.now(timezone.utc), limite=50)
    assert resumen1.revisadas == 1 and resumen1.aceptadas == 1
    assert repo._notas[1].estado == "aceptada" and matias.llamadas == 1
    assert repo.eventos and repo.eventos[0]["evento"] == "reintento_nota_credito"

    # Segunda corrida: la nota ya está aceptada → el repo no la lista → MATIAS no se vuelve a llamar.
    resumen2 = await svc.reintentar_pendientes(antiguedad=datetime.now(timezone.utc), limite=50)
    assert resumen2.revisadas == 0 and matias.llamadas == 1


async def test_reintento_error_persistente_incrementa_intentos():
    nota_err = NotaLeer(
        id=2, factura_id=3, venta_id=10, tipo="nota_credito", motivo=None, prefijo="FPR",
        consecutivo=None, cufe=None, estado="error", idempotency_key="nc-r2", intentos=1,
    )
    repo = _FakeNotasRepo()
    repo.sembrar(nota_err)
    matias = _FakeMatias(excepcion=RuntimeError("timeout"))
    resumen = await _svc(repo, matias).reintentar_pendientes(antiguedad=datetime.now(timezone.utc), limite=50)
    assert resumen.aceptadas == 0 and resumen.sin_cambio == 1
    assert repo._notas[2].estado == "error" and repo._notas[2].intentos == 2


# --- integración: devolución de venta facturada → nota crédito ligada --------
async def test_devolucion_de_venta_facturada_emite_y_liga_nota_credito(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                    "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',20000,12000,12000,19,false,true) RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"), {"p": pid}
        )
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("3"))]),
                vendedor_id=uid,
            )
        ).venta
        # La venta fue transmitida a DIAN: factura aceptada (con CUFE, referenciable por la nota).
        await s.execute(
            text("INSERT INTO facturas_electronicas (venta_id, tipo, prefijo, consecutivo, estado, cufe) "
                 "VALUES (:v,'factura','FPR',1024,'aceptada',:c)"),
            {"v": venta.id, "c": _CUFE_FACT},
        )
        await s.commit()

    matias = _FakeMatias(resultado=EmisionResultado(ok=True, cufe=_CUFE, categoria="aceptada", raw={"ok": 1}))
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        notas = NotasService(SqlNotasRepository(s), matias, _CONFIG)
        svc = DevolucionesService(
            SqlDevolucionesRepository(s), caja=SqlCajaRepository(s),
            fiados=FiadosService(SqlFiadosRepository(s)), notas=notas,
        )
        res = await svc.devolver(DevolucionCrear(venta_id=venta.id, motivo="defectuoso"), usuario_id=uid)
        await s.commit()
        dev_id = res.devolucion.id

    # El UBL construido desde datos reales referenció la factura original (billing_reference).
    assert matias.tipo == "nota_credito"
    assert matias.payload["billing_reference"]["uuid"] == _CUFE_FACT
    assert matias.payload["billing_reference"]["number"] == "FPR1024"

    async with AsyncSession(tenant.engine) as s:
        nota_id, tipo, estado, cufe = (
            await s.execute(
                text("SELECT id, tipo, estado, cufe FROM notas_electronicas WHERE venta_id=:v"), {"v": venta.id}
            )
        ).one()
        assert tipo == "nota_credito" and estado == "aceptada" and cufe == _CUFE
        # La devolución quedó ligada a la nota.
        dev_nota = (
            await s.execute(text("SELECT nota_id FROM devoluciones WHERE id=:d"), {"d": dev_id})
        ).scalar_one()
        assert dev_nota == nota_id
        # Bitácora del evento DIAN.
        eventos = (
            await s.execute(text("SELECT count(*) FROM eventos_dian WHERE evento='emision_nota_credito'"))
        ).scalar_one()
        assert eventos == 1
