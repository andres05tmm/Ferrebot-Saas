"""Cola de impresión (R1 Restaurante Ronda 2, ADR 0033 D2–D3).

Invariantes (test-primero): pedido confirmado con ítems de 2 zonas crea EXACTAMENTE 2 trabajos de
comanda; el reintento de generación NO duplica (idempotencia por `idempotency_key` UNIQUE — una
comanda jamás se imprime dos veces); reimprimir crea un trabajo NUEVO ligado al original; la
superficie `/api/v1/impresion` responde 404 sin el flag; la migración corre up/down limpia.
La cola re-entrega trabajos `entregado_agente` vencidos (corte de conexión) sin duplicar filas.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.tenancy.catalogo import validar_dependencias
from modules.impresion.repository import SqlImpresionRepository
from modules.impresion.service import ImpresionService, TrabajoInexistente
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService


async def _seed(s: AsyncSession) -> dict:
    """Config 24h + zonas parrilla/bar + Hamburguesa→parrilla, Limonada→bar."""
    await s.execute(
        text(
            "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
            "tiempo_estimado_min, costo_domicilio_default) VALUES (true, '00:00', '23:59', 0, 45, 0)"
        )
    )
    ids = {}
    for zona in ("parrilla", "bar"):
        ids[zona] = (
            await s.execute(
                text("INSERT INTO comanda_zonas (nombre, activo) VALUES (:n, true) RETURNING id"),
                {"n": zona},
            )
        ).scalar_one()
    for nombre, precio, zona in (("Hamburguesa", 18000, "parrilla"), ("Limonada", 5000, "bar")):
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo, zona_comanda_id) "
                    "VALUES (:n, 'unidad', :p, 0, false, true, :z) RETURNING id"
                ),
                {"n": nombre, "p": precio, "z": ids[zona]},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 50, 0)"),
            {"p": pid},
        )
        ids[nombre] = pid
    await s.commit()
    return ids


async def _confirmar_pedido(s: AsyncSession) -> int:
    svc = PedidosService(SqlPedidosRepository(s))
    await svc.armar_pedido(
        "3001112233",
        [
            ItemPedido(producto="hamburguesa", cantidad=Decimal("2")),
            ItemPedido(producto="limonada", cantidad=Decimal("1")),
        ],
        ahora=now_co(),
    )
    pedido, _ = await svc.confirmar_pedido(
        "3001112233", direccion="Cra 1 # 2-3", metodo_pago="efectivo"
    )
    await s.commit()
    return pedido.id


def test_flag_impresion_con_dependencia_or():
    # `impresion` vale con cualquiera de sus padres (OR): ventas sola basta.
    assert validar_dependencias(frozenset({"impresion", "ventas"})) == []
    assert validar_dependencias(frozenset({"impresion"})) != []


async def test_pedido_confirmado_crea_un_trabajo_por_comanda_zona(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        pedido_id = await _confirmar_pedido(s)

        filas = (
            await s.execute(
                text(
                    "SELECT tipo, estado, zona_id, comanda_id, payload, idempotency_key "
                    "FROM trabajos_impresion WHERE pedido_id = :p ORDER BY zona_id"
                ),
                {"p": pedido_id},
            )
        ).all()
        # Ítems de 2 zonas → EXACTAMENTE 2 trabajos de comanda (condicional R1).
        assert len(filas) == 2
        assert {f.zona_id for f in filas} == {ids["parrilla"], ids["bar"]}
        assert all(f.tipo == "comanda" and f.estado == "pendiente" for f in filas)
        # Payload determinista y COMPLETO: el agente no consulta negocio.
        parrilla = next(f for f in filas if f.zona_id == ids["parrilla"])
        items = parrilla.payload["items"]
        assert items == [{"nombre": "Hamburguesa", "cantidad": "2", "modificadores": []}]
        assert parrilla.payload["zona"] == "parrilla"


async def test_reintento_de_generacion_no_duplica(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s)
        repo = SqlPedidosRepository(s)
        pedido = await repo.pedido_por_id(pedido_id)
        await s.refresh(pedido, attribute_names=["items"])
        comandas = list(
            (await s.execute(text("SELECT id FROM comandas WHERE pedido_id = :p"), {"p": pedido_id}))
            .scalars()
        )

        # Replay de la generación (reintento del confirm / doble evento): mismas claves → 0 filas nuevas.
        from modules.impresion.generacion import generar_trabajos_comandas

        await generar_trabajos_comandas(s, pedido_id=pedido_id, comanda_ids=comandas)
        await s.commit()
        n = (
            await s.execute(
                text("SELECT count(*) FROM trabajos_impresion WHERE pedido_id = :p"), {"p": pedido_id}
            )
        ).scalar_one()
        assert n == 2


async def test_reimprimir_crea_trabajo_nuevo_ligado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s)
        original = (
            await s.execute(
                text("SELECT id FROM trabajos_impresion WHERE pedido_id = :p ORDER BY id LIMIT 1"),
                {"p": pedido_id},
            )
        ).scalar_one()

        svc = ImpresionService(SqlImpresionRepository(s))
        nuevo = await svc.reimprimir(original)
        await s.commit()
        assert nuevo.id != original
        assert nuevo.reimpresion_de == original
        assert nuevo.estado == "pendiente"
        # Doble clic (misma cuenta de reimpresiones) → NO duplica: devuelve el mismo trabajo.
        repetido = await svc.reimprimir(original)
        assert repetido.id == nuevo.id
        with pytest.raises(TrabajoInexistente):
            await svc.reimprimir(999_999)


async def test_cola_entrega_ackea_y_reentrega_vencidos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s)
        svc = ImpresionService(SqlImpresionRepository(s))

        # La cola entrega los pendientes y los marca `entregado_agente`.
        trabajos = await svc.cola()
        await s.commit()
        assert len(trabajos) == 2
        assert all(t.estado == "entregado_agente" for t in trabajos)
        # Cola inmediata de nuevo: nada (están entregados, no vencidos) — no hay papel doble.
        assert await svc.cola() == []

        # Ack impreso / error.
        await svc.ack(trabajos[0].id, ok=True)
        await svc.ack(trabajos[1].id, ok=False, detalle="sin papel")
        await s.commit()
        filas = (
            await s.execute(
                text(
                    "SELECT id, estado, impreso_en, error_detalle, intentos "
                    "FROM trabajos_impresion WHERE pedido_id = :p ORDER BY id"
                ),
                {"p": pedido_id},
            )
        ).all()
        assert filas[0].estado == "impreso" and filas[0].impreso_en is not None
        assert filas[1].estado == "error" and filas[1].error_detalle == "sin papel"

        # Corte de conexión: un `entregado_agente` VENCIDO vuelve a salir en la cola (sin duplicar).
        await s.execute(
            text(
                "UPDATE trabajos_impresion SET estado = 'entregado_agente', "
                "entregado_en = now() - interval '10 minutes' WHERE id = :t"
            ),
            {"t": filas[1].id},
        )
        await s.commit()
        de_nuevo = await svc.cola()
        assert [t.id for t in de_nuevo] == [filas[1].id]


async def test_endpoint_impresion_gating_y_flujo(tenant):
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.impresion.router import router as impresion_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def _app(caps: frozenset[str]) -> FastAPI:
        app = FastAPI()
        app.include_router(impresion_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(
            user_id=1, tenant="t", rol="vendedor"
        )
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s)

    # Invisible sin flag `impresion`.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(frozenset({"ventas"})), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.get("/api/v1/impresion/cola")).status_code == 404

    caps = frozenset({"impresion", "ventas", "pack_pedidos"})
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps), raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/impresion/cola")
        assert r.status_code == 200, r.text
        trabajos = r.json()
        assert len(trabajos) == 2 and all(t["tipo"] == "comanda" for t in trabajos)

        tid = trabajos[0]["id"]
        assert (await c.post(f"/api/v1/impresion/trabajos/{tid}/ack", json={"ok": True})).status_code == 200
        assert (
            await c.post(
                f"/api/v1/impresion/trabajos/{trabajos[1]['id']}/ack",
                json={"ok": False, "detalle": "sin papel"},
            )
        ).status_code == 200

        re = await c.post(f"/api/v1/impresion/trabajos/{tid}/reimprimir")
        assert re.status_code == 200 and re.json()["reimpresion_de"] == tid
        assert (await c.post("/api/v1/impresion/trabajos/999999/reimprimir")).status_code == 404

        # Precuenta bajo demanda (pedido abierto o confirmado): payload snapshot del pedido.
        pre = await c.post("/api/v1/impresion/trabajos", json={"tipo": "precuenta", "pedido_id": pedido_id})
        assert pre.status_code == 200, pre.text
        assert pre.json()["tipo"] == "precuenta"
        # Replay del mismo POST (doble clic) → el mismo trabajo, no dos.
        pre2 = await c.post("/api/v1/impresion/trabajos", json={"tipo": "precuenta", "pedido_id": pedido_id})
        assert pre2.json()["id"] == pre.json()["id"]


async def test_migracion_0064_up_down(tenant):
    from tools._alembic import downgrade_tenant, upgrade_tenant

    _tbl = "SELECT to_regclass('public.trabajos_impresion') IS NOT NULL"
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0063_recetas_impuestos")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is False

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
