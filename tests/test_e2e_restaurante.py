"""Demo E2E del Pack Restaurante — la "Definición de Hecho global" (goal §A.3, ADR 0032).

Con la carta REAL de Siriuss: (1) cliente WhatsApp simulado pide 2 platos fuertes (proteínas y
acompañantes elegidos) y una sopa a domicilio en Bocagrande → pedido confirmado con recargo por
plato → comandas en KDS por zona → "listo" total notifica al canal (mock) → convertir en venta:
insumos descontados por receta, caja cuadrada en el arqueo, cierre fiscal encolado (mock) y pedido
`entregado`. (2) Paralelo en salón: mesa con 2 rondas → precuenta → cobro con propina → venta única
idempotente y mesa liberada.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool
from ai.pedidos_tools import PedidosDeps, ejecutar
from core.llm.base import ToolCall
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.pedidos.conversion import convertir_pedido
from modules.pedidos.kds import KdsService
from modules.pedidos.mesas import MesasService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService
from tests.carta_siriuss import sembrar_carta_siriuss

_TELEFONO = "573001112233"


def _ctx() -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=1, rol="vendedor", cliente_telefono=_TELEFONO,
        capacidades=frozenset({"pack_pedidos", "pack_mesas", "kds", "recetas", "ventas", "caja"}),
    )


async def test_e2e_whatsapp_kds_venta_con_carta_siriuss(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await sembrar_carta_siriuss(s)
        repo = SqlPedidosRepository(s)
        deps = PedidosDeps(pedidos=PedidosService(repo))

        # 1) El cliente pide por WhatsApp (tools REALES del agente, con modificadores de la carta).
        armado = await ejecutar(
            ToolCall(id="t1", name="armar_pedido", arguments={
                "items": [
                    {"producto": "plato fuerte del día", "cantidad": 1,
                     "modificadores": ["carne asada", "arroz blanco o de coco", "tajadas"]},
                    {"producto": "plato fuerte del día", "cantidad": 1,
                     "modificadores": ["pollo frito", "lentejas"]},
                    {"producto": "sopa de hueso", "cantidad": 1},
                ],
            }),
            _ctx(), deps,
        )
        assert not isinstance(armado, ErrorTool), getattr(armado, "detail", None)
        assert armado.data["subtotal"] == "52000.00"   # 2×19000 + 14000, todo de catálogo

        confirmado = await ejecutar(
            ToolCall(id="t2", name="confirmar_pedido", arguments={
                "direccion": "Cra 2 # 10-50, apto 301", "barrio": "Bocagrande",
                "metodo_pago": "efectivo", "nombre": "Ana", "telefono_contacto": _TELEFONO,
            }),
            _ctx(), deps,
        )
        assert not isinstance(confirmado, ErrorTool), getattr(confirmado, "detail", None)
        pedido_id = confirmado.data["pedido_id"]
        # Recargo POR PLATO Bocagrande: 3000 + 1000×3 = 6000; total 58000.
        assert confirmado.data["domicilio"] == "6000.00"
        assert confirmado.data["total"] == "58000.00"
        await s.commit()

        # 2) Comandas en el KDS por zona (parrilla: 2 platos; sopas: 1 sopa).
        comandas = (
            await s.execute(
                text("SELECT id, zona_id FROM comandas WHERE pedido_id = :p ORDER BY id"),
                {"p": pedido_id},
            )
        ).all()
        assert {c.zona_id for c in comandas} == {ids["zona:parrilla"], ids["zona:sopas"]}

        # 3) "Listo" en todas → notificación al canal del pedido (mock del canal).
        avisados: list[str] = []

        async def _notificar(pedido) -> None:
            avisados.append(pedido.telefono_contacto or pedido.cliente_telefono)

        kds = KdsService(repo, notificar_listo=_notificar)
        for c in comandas:
            await kds.cambiar_estado(c.id, "listo")
        await s.commit()
        assert avisados == [_TELEFONO]

        # 4) Caja abierta → convertir en venta (puente F1 + recetas F6 + fiscal mock).
        caja = CajaService(SqlCajaRepository(s))
        await caja.abrir(usuario_id=ids["usuario"], saldo_inicial=Decimal("50000"))
        await s.commit()

        res = await convertir_pedido(
            pedido_id, repo=repo, ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=ids["usuario"],
        )
        await s.commit()
        assert res.replay is False and res.total == Decimal("58000.00")

        # Insumos descontados por receta (2 platos): arroz 0.4, proteína 0.5; NINGÚN mov. del plato.
        movs = (
            await s.execute(
                text("SELECT producto_id, sum(cantidad) AS c FROM movimientos_inventario "
                     "WHERE tipo='SALIDA' GROUP BY producto_id")
            )
        ).all()
        por_prod = {m.producto_id: m.c for m in movs}
        assert por_prod[ids["Arroz (insumo)"]] == Decimal("0.400")
        assert por_prod[ids["Proteína (insumo)"]] == Decimal("0.500")
        assert ids["Plato fuerte del día"] not in por_prod
        # El detalle snapshotea el impoconsumo (INC 8) y los modificadores en la descripción.
        det = (
            await s.execute(
                text("SELECT descripcion, iva, tipo_impuesto FROM ventas_detalle "
                     "WHERE venta_id = :v AND descripcion LIKE 'Plato%' ORDER BY id"),
                {"v": res.venta_id},
            )
        ).all()
        assert len(det) == 2 and all(d.iva == 8 and d.tipo_impuesto == "inc" for d in det)
        assert "Carne asada" in det[0].descripcion and "Tajadas" in det[0].descripcion

        # Pedido ENTREGADO y caja CUADRADA (venta en efectivo entra por ventas_efectivo).
        estado = (
            await s.execute(text("SELECT estado FROM pedidos WHERE id = :p"), {"p": pedido_id})
        ).scalar_one()
        assert estado == "entregado"
        cierre = await caja.cerrar(usuario_id=ids["usuario"], saldo_contado=Decimal("108000"))
        await s.commit()
        assert cierre.diferencia == Decimal("0.00")


async def test_e2e_salon_mesa_precuenta_cobro_con_propina(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await sembrar_carta_siriuss(s)
        repo = SqlPedidosRepository(s)
        mesa_id = (
            await s.execute(
                text("INSERT INTO mesas (nombre, activo) VALUES ('Mesa 1', true) RETURNING id")
            )
        ).scalar_one()
        await s.commit()

        svc = MesasService(repo)
        await svc.abrir(mesa_id)
        # Ronda 1: plato con proteína; ronda 2: sopa.
        await svc.agregar(mesa_id, [
            ItemPedido(producto="plato fuerte del día", cantidad=Decimal("1"),
                       modificadores=("cerdo asado", "lentejas")),
        ])
        await svc.agregar(mesa_id, [ItemPedido(producto="sopa de hueso", cantidad=Decimal("1"))])
        await s.commit()

        pre = await svc.precuenta(mesa_id)
        assert pre.total == Decimal("33000.00")   # 19000 + 14000

        ventas = VentaService(SqlVentasRepository(s))
        cobro = await svc.cobrar(
            mesa_id, ventas=ventas, usuario_id=ids["usuario"],
            metodo_pago="efectivo", propina=Decimal("3000"),
        )
        await s.commit()
        assert cobro.total == Decimal("36000.00")

        # Venta ÚNICA idempotente (recobrar replaya) y mesa liberada.
        orden_id = (
            await s.execute(text("SELECT id FROM pedidos WHERE venta_id = :v"), {"v": cobro.venta_id})
        ).scalar_one()
        replay = await convertir_pedido(
            orden_id, repo=repo, ventas=ventas, usuario_id=ids["usuario"]
        )
        assert replay.replay is True and replay.venta_id == cobro.venta_id
        n_ventas = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        assert n_ventas == 1
        abiertas = (
            await s.execute(
                text("SELECT count(*) FROM pedidos WHERE mesa_id = :m AND estado = 'abierto'"),
                {"m": mesa_id},
            )
        ).scalar_one()
        assert abiertas == 0


async def test_e2e_conversion_encola_cierre_fiscal_mock(tenant, monkeypatch):
    # El endpoint HTTP de conversión invoca `encolar_cierre_pos` best-effort (ADR 0014) tras la venta.
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    import modules.pedidos.router as pedidos_router_mod
    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db

    encolados: list[int] = []

    async def _encolar_mock(request, session, venta_id) -> None:
        encolados.append(venta_id)

    monkeypatch.setattr(pedidos_router_mod, "encolar_cierre_pos", _encolar_mock)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await sembrar_carta_siriuss(s)
        svc = PedidosService(SqlPedidosRepository(s))
        from core.config.timezone import now_co
        await svc.armar_pedido(
            _TELEFONO, [ItemPedido(producto="sopa de hueso", cantidad=Decimal("1"))], ahora=now_co()
        )
        pedido, _ = await svc.confirmar_pedido(
            _TELEFONO, direccion="Cl 5 # 3-21", metodo_pago="efectivo"
        )
        await s.commit()
        pedido_id = pedido.id

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = FastAPI()
    app.include_router(pedidos_router_mod.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol="vendedor")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pack_pedidos", "ventas"})

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.post(f"/api/v1/pedidos/{pedido_id}/convertir", json={})
        assert r.status_code == 201, r.text
    assert encolados == [r.json()["venta_id"]]   # cierre fiscal ENCOLADO (mock), fuera de la tx
