"""Comprobante de pago por foto (plan demo Sirius §4): registro + asociación + desempate.

`registrar_comprobante` SIEMPRE guarda la fila de auditoría y asocia al cobro pendiente del cliente
(sin marcar pagado — una captura es falsificable). El conciliador usa el comprobante para desempatar
cuando ≥2 cobros comparten monto. Base efímera real (fixture `tenant`); SSE/notificaciones se capturan
con callbacks (cero red).
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.vision.recibo import ReciboExtraido
from modules.pagos.comprobantes import registrar_comprobante
from modules.pagos.conciliador_transferencias import conciliar_transferencia
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService

TEL = "tg:987654"
TEL2 = "tg:111222"


class _Capturas:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, dict]] = []
        self.cliente: list[tuple[str, str]] = []
        self.negocio: list[str] = []

    async def publicar(self, session, event, data):
        self.eventos.append((event, data))

    async def notificar_cliente(self, telefono, texto):
        self.cliente.append((telefono, texto))

    async def notificar_negocio(self, texto):
        self.negocio.append(texto)


async def _crear_cobro_pedido(s, *, origen_id, monto, telefono=TEL):
    svc = PagosService(SqlPagosRepository(s))
    return await svc.crear_cobro(
        origen="pedido", origen_id=origen_id, monto=Decimal(monto),
        descripcion=f"Pedido #{origen_id}", cliente_telefono=telefono,
    )


def _recibo(valor, *, confianza="0.95"):
    return ReciboExtraido(
        valor=None if valor is None else Decimal(valor), confianza=Decimal(confianza),
        tipo_transaccion="transferencia", referencia="APR-123",
    )


async def _comprobantes(s):
    return (await s.execute(text(
        "SELECT cliente_telefono, cobro_id, monto FROM comprobantes_pago ORDER BY id"))).all()


# --- registro: matching -------------------------------------------------------------
async def test_registrar_asocia_un_cobro_monto_igual(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cobro = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()
        r = await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("25000"),
                                        imagen_ref="img/1.jpg")
        await s.commit()
        filas = await _comprobantes(s)

    assert r.estado == "asociado" and r.cobro is not None and r.cobro.id == cobro.id
    assert "cocina" in r.mensaje_cliente
    # SIEMPRE guarda la fila, con el cobro asociado; y NO marca pagado.
    assert len(filas) == 1 and filas[0].cobro_id == cobro.id
    async with AsyncSession(tenant.engine) as s:
        estado = (await s.execute(text("SELECT estado FROM cobros WHERE id=:i"),
                                  {"i": cobro.id})).scalar_one()
    assert estado == "pendiente"   # el comprobante JAMÁS paga


async def test_registrar_ilegible_no_asocia_pero_audita(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()
        # confianza baja → ilegible aun con cobro que calzaría
        r = await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("25000", confianza="0.3"))
        await s.commit()
        filas = await _comprobantes(s)

    assert r.estado == "ilegible" and r.cobro is None
    assert len(filas) == 1 and filas[0].cobro_id is None   # auditado sin asociar


async def test_registrar_sin_pedido_pendiente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("25000"))
        await s.commit()
        filas = await _comprobantes(s)

    assert r.estado == "sin_match" and r.cobro is None
    assert len(filas) == 1 and filas[0].cobro_id is None


async def test_registrar_dos_cobros_monto_discrimina(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        c1 = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await _crear_cobro_pedido(s, origen_id=2, monto="18000")
        await s.commit()
        r = await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("25000"))
        await s.commit()

    assert r.estado == "asociado" and r.cobro is not None and r.cobro.id == c1.id


# --- desempate del conciliador ------------------------------------------------------
async def test_desempate_comprobante_paga_ese_y_notifica_a_ese_cliente(tenant):
    """2 cobros del MISMO monto de clientes distintos; uno tiene comprobante → la transferencia
    paga ESE y notifica a ese cliente."""
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        con = await _crear_cobro_pedido(s, origen_id=1, monto="30000", telefono=TEL)
        sin = await _crear_cobro_pedido(s, origen_id=2, monto="30000", telefono=TEL2)
        await s.commit()
        # el cliente TEL manda su comprobante → se asocia a su cobro
        await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("30000"))
        await s.commit()

        marcado = await conciliar_transferencia(
            s, monto=Decimal("30000"),
            notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
            publicar=cap.publicar,
        )
        await s.commit()
        estados = dict((await s.execute(
            text("SELECT id, estado FROM cobros WHERE id IN (:a,:b)"),
            {"a": con.id, "b": sin.id})).all())

    assert marcado is not None and marcado.id == con.id
    assert estados[con.id] == "pagado" and estados[sin.id] == "pendiente"
    assert cap.eventos == [("pedido_pagado", {"pedido_id": 1, "cobro_id": con.id, "monto": "30000"})]
    assert len(cap.cliente) == 1 and cap.cliente[0][0] == TEL   # avisó al dueño del comprobante


async def test_desempate_ambos_con_comprobante_no_toca_nada(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        c1 = await _crear_cobro_pedido(s, origen_id=1, monto="30000", telefono=TEL)
        c2 = await _crear_cobro_pedido(s, origen_id=2, monto="30000", telefono=TEL2)
        await s.commit()
        await registrar_comprobante(s, cliente_telefono=TEL, datos=_recibo("30000"))
        await registrar_comprobante(s, cliente_telefono=TEL2, datos=_recibo("30000"))
        await s.commit()

        marcado = await conciliar_transferencia(
            s, monto=Decimal("30000"),
            notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
            publicar=cap.publicar,
        )
        await s.commit()
        estados = dict((await s.execute(
            text("SELECT id, estado FROM cobros WHERE id IN (:a,:b)"),
            {"a": c1.id, "b": c2.id})).all())

    assert marcado is None and cap.eventos == [] and cap.cliente == []
    assert estados[c1.id] == "pendiente" and estados[c2.id] == "pendiente"


async def test_replay_conciliador_no_renotifica(tenant):
    """Segundo disparo del conciliador con el mismo monto: el cobro ya está pagado (fuera del pool
    de pendientes) → no re-marca ni re-notifica."""
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cobro = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()
        m1 = await conciliar_transferencia(
            s, monto=Decimal("25000"), notificar_cliente=cap.notificar_cliente,
            notificar_negocio=cap.notificar_negocio, publicar=cap.publicar)
        await s.commit()
        m2 = await conciliar_transferencia(
            s, monto=Decimal("25000"), notificar_cliente=cap.notificar_cliente,
            notificar_negocio=cap.notificar_negocio, publicar=cap.publicar)
        await s.commit()

    assert m1 is not None and m1.id == cobro.id and m2 is None
    assert len(cap.eventos) == 1 and len(cap.cliente) == 1   # una sola cascada
