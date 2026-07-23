"""Comandas KDS (F4 Pack Restaurante, ADR 0032 D5): zonas de cocina + cola por comanda.

Invariantes (test-primero): un pedido confirmado con ítems de 2 zonas genera comandas SEPARADAS por
zona; las transiciones de estado son válidas y auditadas (timestamps); "listo" en TODAS las
comandas del pedido dispara la notificación al canal (puerto mockeado). El KDS es una VISTA sobre
los mismos datos del pedido — no duplica precios ni toca stock/caja.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.pedidos.kds import ComandaInexistente, KdsService, TransicionComandaInvalida
from modules.pedidos.mesas import MesasService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService


async def _seed(s: AsyncSession) -> dict:
    """Config 24h + zonas parrilla/bar + Hamburguesa→parrilla, Limonada→bar, Postre sin zona."""
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
    for nombre, precio, zona in (
        ("Hamburguesa", 18000, "parrilla"), ("Limonada", 5000, "bar"), ("Postre", 8000, None),
    ):
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo, zona_comanda_id) "
                    "VALUES (:n, 'unidad', :p, 0, false, true, :z) RETURNING id"
                ),
                {"n": nombre, "p": precio, "z": ids[zona] if zona else None},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 50, 0)"),
            {"p": pid},
        )
        ids[nombre] = pid
    await s.commit()
    return ids


async def _confirmar_pedido(s: AsyncSession, items: list[ItemPedido]) -> int:
    svc = PedidosService(SqlPedidosRepository(s))
    await svc.armar_pedido("3001112233", items, ahora=now_co())
    pedido, _ = await svc.confirmar_pedido(
        "3001112233", direccion="Cra 1 # 2-3", metodo_pago="efectivo"
    )
    await s.commit()
    return pedido.id


async def test_pedido_confirmado_genera_comandas_por_zona(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        pedido_id = await _confirmar_pedido(s, [
            ItemPedido(producto="hamburguesa", cantidad=Decimal("2")),
            ItemPedido(producto="limonada", cantidad=Decimal("1")),
            ItemPedido(producto="postre", cantidad=Decimal("1")),
        ])

        filas = (
            await s.execute(
                text(
                    "SELECT c.id, c.zona_id, c.estado, count(ci.id) AS n FROM comandas c "
                    "LEFT JOIN comanda_items ci ON ci.comanda_id = c.id "
                    "WHERE c.pedido_id = :p GROUP BY c.id, c.zona_id, c.estado ORDER BY c.zona_id"
                ),
                {"p": pedido_id},
            )
        ).all()
        # 3 comandas: parrilla (hamburguesa), bar (limonada) y cocina general (postre, zona NULL).
        assert len(filas) == 3
        assert {f.zona_id for f in filas} == {ids["parrilla"], ids["bar"], None}
        assert all(f.estado == "pendiente" and f.n == 1 for f in filas)


async def test_ronda_de_mesa_genera_comandas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        mesa_id = (
            await s.execute(
                text("INSERT INTO mesas (nombre, activo) VALUES ('Mesa 1', true) RETURNING id")
            )
        ).scalar_one()
        await s.commit()
        svc = MesasService(SqlPedidosRepository(s))
        await svc.abrir(mesa_id)
        await svc.agregar(mesa_id, [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"))])
        await svc.agregar(mesa_id, [ItemPedido(producto="limonada", cantidad=Decimal("1"))])
        await s.commit()

        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM comandas c JOIN pedidos p ON p.id = c.pedido_id "
                    "WHERE p.mesa_id = :m"
                ),
                {"m": mesa_id},
            )
        ).scalar_one()
        assert n == 2   # una comanda por ronda (cada una en su zona)


async def test_transiciones_auditadas_y_listo_notifica(tenant):
    avisados: list[int] = []

    async def _notificar(pedido) -> None:
        avisados.append(pedido.id)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s, [
            ItemPedido(producto="hamburguesa", cantidad=Decimal("1")),
            ItemPedido(producto="limonada", cantidad=Decimal("1")),
        ])
        kds = KdsService(SqlPedidosRepository(s), notificar_listo=_notificar)
        comandas = [
            f.id for f in (
                await s.execute(
                    text("SELECT id FROM comandas WHERE pedido_id = :p ORDER BY id"), {"p": pedido_id}
                )
            ).all()
        ]
        assert len(comandas) == 2

        # pendiente → en_preparacion → listo (timestamps auditados).
        await kds.cambiar_estado(comandas[0], "en_preparacion")
        await kds.cambiar_estado(comandas[0], "listo")
        fila = (
            await s.execute(
                text("SELECT estado, iniciada_en, lista_en FROM comandas WHERE id = :c"),
                {"c": comandas[0]},
            )
        ).one()
        assert fila.estado == "listo" and fila.iniciada_en is not None and fila.lista_en is not None
        assert avisados == []   # aún falta la comanda del bar

        # Transición inválida: listo no retrocede.
        with pytest.raises(TransicionComandaInvalida):
            await kds.cambiar_estado(comandas[0], "pendiente")
        with pytest.raises(ComandaInexistente):
            await kds.cambiar_estado(999_999, "listo")

        # La última comanda en listo → notificación al canal del pedido (mock).
        await kds.cambiar_estado(comandas[1], "listo")   # pendiente → listo directo (plancha rápida)
        await s.commit()
        assert avisados == [pedido_id]


async def test_migracion_0062_up_down(tenant):
    from tools._alembic import downgrade_tenant, upgrade_tenant

    _tbl = "SELECT to_regclass('public.comandas') IS NOT NULL"
    _col = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='productos' AND column_name='zona_comanda_id'"
    )
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
        assert (await s.execute(text(_col))).scalar_one() == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0061_mesas")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is False
        assert (await s.execute(text(_col))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True


async def test_endpoint_kds_gating_y_flujo(tenant):
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.pedidos.kds_router import router as kds_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def _app(caps: frozenset[str], rol: str = "vendedor") -> FastAPI:
        app = FastAPI()
        app.include_router(kds_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol=rol)
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        pedido_id = await _confirmar_pedido(s, [
            ItemPedido(producto="hamburguesa", cantidad=Decimal("1")),
        ])

    # KDS invisible sin flag `kds`.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(frozenset({"pack_pedidos"})), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.get("/api/v1/kds")).status_code == 404

    caps = frozenset({"kds", "pack_pedidos", "ventas"})
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps), raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/api/v1/kds")
        assert r.status_code == 200, r.text
        comandas = r.json()["comandas"]
        assert len(comandas) == 1 and comandas[0]["pedido_id"] == pedido_id
        assert comandas[0]["items"][0]["nombre"] == "Hamburguesa"

        cid = comandas[0]["id"]
        ok = await c.put(f"/api/v1/kds/comandas/{cid}/estado", json={"estado": "en_preparacion"})
        assert ok.status_code == 200 and ok.json()["estado"] == "en_preparacion"
        # Transición inválida → 409; comanda inexistente → 404.
        assert (
            await c.put(f"/api/v1/kds/comandas/{cid}/estado", json={"estado": "pendiente"})
        ).status_code == 409
        assert (
            await c.put("/api/v1/kds/comandas/999999/estado", json={"estado": "listo"})
        ).status_code == 404

    # Zonas y ruteo producto→zona son de admin.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps, rol="vendedor"), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.post("/api/v1/kds/zonas", json={"nombre": "horno"})).status_code == 403
