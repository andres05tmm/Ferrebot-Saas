"""Notas crédito/débito (ADR 0026, Fase 3 Contable B).

Cubre el servicio PURO (fakes, sin BD): idempotencia, desenlaces aceptada/rechazada/error y la bitácora
en `eventos_dian`; y una integración contra base efímera real que ata la devolución de una venta
FACTURADA a su nota crédito. Los tests NUNCA llaman al MATIAS real (usan los fakes existentes).
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.schemas import DevolucionCrear
from modules.devoluciones.service import DevolucionesService
from modules.facturacion.matias_client import EmisionResultado
from modules.facturacion.notas import NotaLeer, NotasService, SqlNotasRepository
from modules.facturacion.service import ConfigFiscal
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService

_CONFIG = ConfigFiscal(resolution_number="18760000001", prefix="FPR", notes="PR", city_id_default="149")
_CUFE = "c" * 40


class _FakeNotasRepo:
    """Repo fake en memoria que satisface `NotasRepo`."""

    def __init__(self, *, existente=None):
        self._existente = existente
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


class _FakeMatias:
    def __init__(self, *, resultado=None, excepcion=None):
        self._resultado = resultado
        self._excepcion = excepcion
        self.llamado = False

    async def emitir_factura(self, payload):
        self.llamado = True
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
        # La venta fue transmitida a DIAN: factura aceptada.
        await s.execute(
            text("INSERT INTO facturas_electronicas (venta_id, tipo, estado, cufe) VALUES (:v,'factura','aceptada',:c)"),
            {"v": venta.id, "c": "f" * 40},
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
