"""Puente transferencia entrante → cobro de pedido → pedido pagado (plan demo Sirius §4).

Cubre la REGLA DURA del conciliador (candidato único), la cascada compartida (SSE `pedido_pagado` +
notificaciones), la idempotencia de la ingesta (replay no re-marca ni re-notifica), el cierre manual
que dispara la misma cascada, y el cobro por transferencia al confirmar un pedido (part a).

Base efímera real (fixture `tenant`); las notificaciones y el SSE se capturan con callbacks/fake
publicar (cero red). El SSE del contrato es exactamente `pedido_pagado` con {pedido_id, cobro_id, monto}.
"""
import base64
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import ai.pedidos_tools as pedidos_tools
from ai.envelope import Contexto, Resultado
from ai.pedidos_tools import ejecutar as pedidos_ejecutar
from core.llm.base import ToolCall
from modules.bancos.gmail.ingesta import procesar_push
from modules.bancos.repository import SqlBancosRepository
from modules.pagos.conciliador_transferencias import (
    cascada_pedido_pagado,
    conciliar_transferencia,
)
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import PedidosService

TEL = "tg:987654"


class _Capturas:
    """Recoge SSE emitidos + notificaciones (cliente/negocio) para asertar la cascada sin red."""

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


# --- conciliador: regla del candidato único -------------------------------------------
async def test_candidato_unico_marca_pagado_emite_sse_y_notifica(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cobro = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()

        marcado = await conciliar_transferencia(
            s, monto=Decimal("25000"),
            notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
            publicar=cap.publicar,
        )
        await s.commit()
        estado = (await s.execute(text("SELECT estado FROM cobros WHERE id=:i"),
                                  {"i": cobro.id})).scalar_one()

    assert marcado is not None and marcado.id == cobro.id and estado == "pagado"
    assert cap.eventos == [("pedido_pagado", {"pedido_id": 1, "cobro_id": cobro.id, "monto": "25000"})]
    assert len(cap.cliente) == 1 and cap.cliente[0][0] == TEL and "entró a cocina" in cap.cliente[0][1]
    assert len(cap.negocio) == 1


async def test_cero_candidatos_no_toca_nada(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()
        # Monto que no calza con ningún cobro pendiente.
        marcado = await conciliar_transferencia(
            s, monto=Decimal("99000"),
            notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
            publicar=cap.publicar,
        )
    assert marcado is None and cap.eventos == [] and cap.cliente == [] and cap.negocio == []


async def test_ambiguo_dos_candidatos_no_toca_nada(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        c1 = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        c2 = await _crear_cobro_pedido(s, origen_id=2, monto="25000")
        await s.commit()
        marcado = await conciliar_transferencia(
            s, monto=Decimal("25000"),
            notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
            publicar=cap.publicar,
        )
        await s.commit()
        estados = dict((await s.execute(
            text("SELECT id, estado FROM cobros WHERE id IN (:a,:b)"), {"a": c1.id, "b": c2.id})).all())

    assert marcado is None and cap.eventos == []
    assert estados[c1.id] == "pendiente" and estados[c2.id] == "pendiente"


# --- cascada compartida directa (contrato del SSE) ----------------------------------
async def test_cascada_ignora_cobro_que_no_es_pedido(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = PagosService(SqlPagosRepository(s))
        cobro = await svc.crear_cobro(origen="cobranza", origen_id=9, monto=Decimal("5000"),
                                      descripcion="Saldo", cliente_telefono=TEL)
        await cascada_pedido_pagado(s, cobro, notificar_cliente=cap.notificar_cliente,
                                    notificar_negocio=cap.notificar_negocio, publicar=cap.publicar)
    assert cap.eventos == [] and cap.cliente == []   # no es pedido → no-op


# --- cierre manual dispara la MISMA cascada ------------------------------------------
async def test_marcar_pagado_manual_pedido_dispara_cascada(tenant):
    cap = _Capturas()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cobro = await _crear_cobro_pedido(s, origen_id=7, monto="18000")
        svc = PagosService(SqlPagosRepository(s))
        cerrado = await svc.marcar_pagado_manual(
            cobro.id, notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio)
        await s.commit()
    assert cerrado.estado == "pagado"
    assert len(cap.cliente) == 1 and cap.cliente[0][0] == TEL   # cliente avisado por cierre manual


# --- idempotencia de la ingesta (replay del mismo correo) ----------------------------
_BODY = ("recibiste un pago de PEDRO PEREZ por $25.000 en tu cuenta *3891 "
         "el 01/07/2026 a las 10:15. Con codigo QR es facil.")


class _FakeCliente:
    def __init__(self, message_id="msg-demo"):
        self._mid = message_id
        self.refresh_token_rotado = None

    async def ids_desde_history(self, history_id):
        return [self._mid]

    async def headers(self, message_id):
        return [{"name": "From", "value": "notificaciones@bancolombia.com.co"},
                {"name": "Subject", "value": "Recibiste una transferencia"}]

    async def mensaje_completo(self, message_id):
        data = base64.urlsafe_b64encode(_BODY.encode()).decode().rstrip("=")
        return {"payload": {"parts": [{"mimeType": "text/plain", "body": {"data": data}}]}}


async def test_replay_ingesta_no_remarca_ni_renotifica(tenant):
    cap = _Capturas()

    async def _run(s, last, push):
        async def al_insertar(mov):
            await conciliar_transferencia(
                s, monto=mov.monto,
                notificar_cliente=cap.notificar_cliente, notificar_negocio=cap.notificar_negocio,
                publicar=cap.publicar,
            )
        return await procesar_push(
            cliente=_FakeCliente(), repo=SqlBancosRepository(s), last_history_id=last,
            notificar=lambda t: _noop(), al_insertar=al_insertar, history_id_push=push,
        )

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cobro = await _crear_cobro_pedido(s, origen_id=1, monto="25000")
        await s.commit()
        r1 = await _run(s, "100", "200")
        await s.commit()
        r2 = await _run(s, "200", "300")   # mismo gmail_message_id → dedup
        await s.commit()
        estado = (await s.execute(text("SELECT estado FROM cobros WHERE id=:i"),
                                  {"i": cobro.id})).scalar_one()

    assert r1.insertados == 1 and r2.insertados == 0
    assert estado == "pagado"
    assert len(cap.eventos) == 1 and len(cap.cliente) == 1   # una sola cascada, sin re-notificar


async def _noop():
    return None


# --- part (a): cobro por transferencia al confirmar el pedido -------------------------
async def _seed_menu_y_config(s):
    pid = (await s.execute(text(
        "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
        "VALUES ('Almuerzo', 'unidad', 18000, 0, false, true) RETURNING id"))).scalar_one()
    await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) "
                         "VALUES (:p, 50, 0)"), {"p": pid})
    await s.commit()
    from datetime import time
    repo = SqlPedidosRepository(s)
    config = await repo.obtener_config()
    config.hora_apertura = time(0, 0)
    config.hora_cierre = time(23, 59)
    config.minimo_pedido = Decimal("0")
    await s.commit()
    return repo


async def test_confirmar_pedido_transferencia_crea_cobro_manual_y_muestra_datos(tenant, monkeypatch):
    async def _fake_datos_pago(tenant_id):
        return "Sirius SAS", "300 111 2222"
    monkeypatch.setattr(pedidos_tools, "_leer_datos_pago", _fake_datos_pago)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = await _seed_menu_y_config(s)
        deps = pedidos_tools.PedidosDeps(
            pedidos=PedidosService(repo),
            pagos=PagosService(SqlPagosRepository(s)),   # modo MANUAL (sin PSP)
        )
        ctx = Contexto(
            tenant_id=1, usuario_id=0, rol="cliente", origen="telegram",
            capacidades=frozenset({"pack_pedidos"}), cliente_telefono=TEL,   # SIN pagos_online
        )
        await pedidos_ejecutar(
            ToolCall(id="t", name="armar_pedido",
                     arguments={"items": [{"producto": "Almuerzo", "cantidad": 1}]}),
            ctx, deps)
        r = await pedidos_ejecutar(
            ToolCall(id="t", name="confirmar_pedido",
                     arguments={"direccion": "Cll 1 # 2-3", "metodo_pago": "transferencia"}),
            ctx, deps)
        await s.commit()

        cobros = (await s.execute(text(
            "SELECT origen, estado, proveedor, url FROM cobros"))).all()
        # Idempotencia por (origen, origen_id): re-crear el mismo cobro no duplica.
        pedido_id = r.data["pedido_id"]
        again = await PagosService(SqlPagosRepository(s)).crear_cobro(
            origen="pedido", origen_id=pedido_id, monto=Decimal(r.data["total"]),
            descripcion="dup", cliente_telefono=TEL)
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM cobros"))).scalar_one()

    assert isinstance(r, Resultado)
    assert len(cobros) == 1 and cobros[0] == ("pedido", "pendiente", "manual", None)
    assert r.data["cobro"]["url"] is None and r.data["cobro"]["estado"] == "pendiente"
    assert "EXACTAMENTE" in r.resumen and "300 111 2222" in r.resumen
    assert again.id == r.data["cobro"]["cobro_id"] and n == 1   # idempotente
