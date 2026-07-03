"""Frente de pagos (ADR 0013) — servicio + adaptador Bold (fakes) + integración con pedidos.

Cubre: cobro manual sin PSP, cobro con link vía PSP falso, idempotencia por (origen, origen_id),
conciliación por polling (pendiente/pagado/vencido + fallo de red no tumba), cierre manual del
dashboard, el parseo/normalización del cliente Bold contra respuestas simuladas, y que confirmar un
pedido con `pagos_online` crea el cobro y manda el link al cliente.
"""
from datetime import time
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, Resultado
from ai.pedidos_tools import PedidosDeps, ejecutar as pedidos_ejecutar
from core.llm.base import ToolCall
from core.pagos.bold import BoldClient, BoldCredenciales
from core.pagos.ports import LinkCobro, SolicitudCobro
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import PedidosService

TEL = "3001112233"


class FakePsp:
    """PSP de prueba: registra solicitudes y devuelve estados programados por proveedor_id."""

    def __init__(self) -> None:
        self.creados: list[SolicitudCobro] = []
        self.estados: dict[str, str] = {}

    async def crear_link(self, solicitud: SolicitudCobro) -> LinkCobro:
        self.creados.append(solicitud)
        pid = f"LNK_{len(self.creados)}"
        self.estados[pid] = "pendiente"
        return LinkCobro(proveedor_id=pid, url=f"https://checkout.bold.co/{pid}")

    async def consultar(self, proveedor_id: str) -> str:
        estado = self.estados[proveedor_id]
        if estado == "boom":
            raise RuntimeError("red caída")
        return estado


# --- servicio ------------------------------------------------------------------
async def test_cobro_manual_sin_psp(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = PagosService(SqlPagosRepository(s))
        cobro = await svc.crear_cobro(
            origen="manual", origen_id=None, monto=Decimal("50000"), descripcion="Anticipo"
        )
        await s.commit()
    assert cobro.proveedor == "manual" and cobro.url is None and cobro.estado == "pendiente"


async def test_cobro_con_psp_e_idempotencia_por_origen(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        psp = FakePsp()
        svc = PagosService(SqlPagosRepository(s), psp=psp)
        c1 = await svc.crear_cobro(
            origen="pedido", origen_id=7, monto=Decimal("39000"), descripcion="Pedido #7"
        )
        c2 = await svc.crear_cobro(   # mismo origen → devuelve el existente, NO crea otro link
            origen="pedido", origen_id=7, monto=Decimal("39000"), descripcion="Pedido #7"
        )
        await s.commit()
    assert c1.proveedor == "bold" and c1.url.startswith("https://checkout.bold.co/")
    assert c2.id == c1.id and len(psp.creados) == 1
    assert psp.creados[0].referencia.startswith("pedido-7-")


async def test_conciliar_transiciones_y_fallo_no_tumba(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        psp = FakePsp()
        svc = PagosService(SqlPagosRepository(s), psp=psp)
        pagado = await svc.crear_cobro(origen="pedido", origen_id=1, monto=Decimal("10000"), descripcion="a")
        sigue = await svc.crear_cobro(origen="pedido", origen_id=2, monto=Decimal("20000"), descripcion="b")
        vencido = await svc.crear_cobro(origen="pedido", origen_id=3, monto=Decimal("30000"), descripcion="c")
        caido = await svc.crear_cobro(origen="pedido", origen_id=4, monto=Decimal("40000"), descripcion="d")
        psp.estados[pagado.proveedor_id] = "pagado"
        psp.estados[vencido.proveedor_id] = "vencido"
        psp.estados[caido.proveedor_id] = "boom"

        resumen = await svc.conciliar()
        await s.commit()

        estados = {
            fila.id: fila.estado
            for fila in (await s.execute(text("SELECT id, estado FROM cobros"))).all()
        }
    assert resumen.pagados == 1 and resumen.cerrados == 1
    assert estados[pagado.id] == "pagado" and estados[vencido.id] == "vencido"
    assert estados[sigue.id] == "pendiente" and estados[caido.id] == "pendiente"   # se reintenta


async def test_marcar_pagado_manual(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = PagosService(SqlPagosRepository(s))
        cobro = await svc.crear_cobro(
            origen="cobranza", origen_id=9, monto=Decimal("80000"), descripcion="Saldo"
        )
        cerrado = await svc.marcar_pagado_manual(cobro.id)
        await s.commit()
    assert cerrado.estado == "pagado"


# --- cliente Bold (HTTP simulado) -------------------------------------------------
class _Resp:
    def __init__(self, status_code: int, data) -> None:
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self, respuestas: list[_Resp]) -> None:
        self._respuestas = respuestas
        self.requests: list[tuple[str, str, dict | None, dict]] = []

    async def request(self, metodo, url, *, json=None, headers=None):
        self.requests.append((metodo, url, json, headers or {}))
        return self._respuestas.pop(0)


async def test_bold_client_crea_y_normaliza_estados():
    http = _FakeHttp([
        _Resp(200, {"payload": {"payment_link": "LNK_abc", "url": "https://checkout.bold.co/LNK_abc"}}),
        _Resp(200, {"payload": {"status": "PAID"}}),
        _Resp(200, {"payload": {"status": "EXPIRED"}}),
    ])
    cliente = BoldClient(BoldCredenciales(api_key="k-test"), client=http)

    link = await cliente.crear_link(SolicitudCobro(
        referencia="pedido-7-abc", monto=Decimal("39000"), descripcion="Pedido #7"
    ))
    assert link.proveedor_id == "LNK_abc"
    assert await cliente.consultar("LNK_abc") == "pagado"
    assert await cliente.consultar("LNK_abc") == "vencido"

    metodo, url, cuerpo, headers = http.requests[0]
    assert metodo == "POST" and url.endswith("/online/link/v1")
    assert headers["Authorization"] == "x-api-key k-test"
    assert cuerpo["amount_type"] == "CLOSE" and cuerpo["reference"] == "pedido-7-abc"
    assert cuerpo["amount"]["total_amount"] == 39000.0


# --- integración con pedidos --------------------------------------------------------
async def test_confirmar_pedido_con_pagos_online_crea_cobro_con_link(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo) VALUES ('Pizza', 'unidad', 30000, 19, false, true) "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 10, 0)"),
            {"p": pid},
        )
        await s.commit()
        repo = SqlPedidosRepository(s)
        config = await repo.obtener_config()
        config.hora_apertura = time(0, 0)
        config.hora_cierre = time(23, 59)
        await s.commit()

        deps = PedidosDeps(
            pedidos=PedidosService(repo),
            pagos=PagosService(SqlPagosRepository(s), psp=FakePsp()),
        )
        ctx = Contexto(
            tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
            capacidades=frozenset({"pack_pedidos", "pagos_online"}), cliente_telefono=TEL,
        )
        await pedidos_ejecutar(
            ToolCall(id="t", name="armar_pedido",
                     arguments={"items": [{"producto": "Pizza", "cantidad": 1}]}),
            ctx, deps,
        )
        r = await pedidos_ejecutar(
            ToolCall(id="t", name="confirmar_pedido",
                     arguments={"direccion": "Cra 1 # 2-3", "metodo_pago": "transferencia"}),
            ctx, deps,
        )
        await s.commit()

    assert isinstance(r, Resultado)
    assert r.data["cobro"]["url"].startswith("https://checkout.bold.co/")
    assert "Puede pagar de una vez aquí" in r.resumen


async def test_confirmar_pedido_sin_capacidad_no_crea_cobro(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo) VALUES ('Pizza', 'unidad', 30000, 19, false, true) "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 10, 0)"),
            {"p": pid},
        )
        await s.commit()
        repo = SqlPedidosRepository(s)
        config = await repo.obtener_config()
        config.hora_apertura = time(0, 0)
        config.hora_cierre = time(23, 59)
        await s.commit()

        # `pagos` inyectado pero SIN la capacidad: el guardarraíl es la capacidad, no el wiring.
        deps = PedidosDeps(
            pedidos=PedidosService(repo),
            pagos=PagosService(SqlPagosRepository(s), psp=FakePsp()),
        )
        ctx = Contexto(
            tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
            capacidades=frozenset({"pack_pedidos"}), cliente_telefono=TEL,
        )
        await pedidos_ejecutar(
            ToolCall(id="t", name="armar_pedido",
                     arguments={"items": [{"producto": "Pizza", "cantidad": 1}]}),
            ctx, deps,
        )
        r = await pedidos_ejecutar(
            ToolCall(id="t", name="confirmar_pedido",
                     arguments={"direccion": "Cra 1 # 2-3", "metodo_pago": "efectivo"}),
            ctx, deps,
        )
        await s.commit()
        cobros = (await s.execute(text("SELECT count(*) FROM cobros"))).scalar_one()

    assert isinstance(r, Resultado) and "cobro" not in r.data
    assert cobros == 0


# --- máquina de estados (solo `pendiente` es mutable) ------------------------------
async def test_transiciones_invalidas_rechazadas(tenant):
    import pytest

    from modules.pagos.service import TransicionInvalida

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = PagosService(SqlPagosRepository(s))
        cancelado = await svc.crear_cobro(
            origen="cobranza", origen_id=21, monto=Decimal("10000"), descripcion="x"
        )
        await svc.cancelar(cancelado.id)
        with pytest.raises(TransicionInvalida):   # pagar un cancelado re-emitiría cobro_pagado
            await svc.marcar_pagado_manual(cancelado.id)

        pagado = await svc.crear_cobro(
            origen="cobranza", origen_id=22, monto=Decimal("20000"), descripcion="y"
        )
        await svc.marcar_pagado_manual(pagado.id)
        with pytest.raises(TransicionInvalida):   # cancelar un pagado contradice el hecho emitido
            await svc.cancelar(pagado.id)
        with pytest.raises(TransicionInvalida):   # re-pagar re-emitiría el evento
            await svc.marcar_pagado_manual(pagado.id)
        await s.commit()


async def test_crear_cobro_reabre_cancelado_con_link_nuevo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        psp = FakePsp()
        svc = PagosService(SqlPagosRepository(s), psp=psp)
        c1 = await svc.crear_cobro(
            origen="pedido", origen_id=33, monto=Decimal("15000"), descripcion="Pedido #33"
        )
        await svc.cancelar(c1.id)
        # El UNIQUE parcial por (origen, origen_id) impide otra fila: el cancelado se REABRE.
        c2 = await svc.crear_cobro(
            origen="pedido", origen_id=33, monto=Decimal("18000"), descripcion="Pedido #33 v2"
        )
        await s.commit()
    assert c2.id == c1.id and c2.estado == "pendiente"
    assert c2.monto == Decimal("18000") and len(psp.creados) == 2   # link fresco
