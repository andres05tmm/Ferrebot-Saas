"""Menú digital QR público (F5 Pack Restaurante, ADR 0032 D6).

Invariantes (test-primero): la página responde SIN token, jamás contiene datos de otro tenant ni
campos internos (aislamiento multi-tenant explícito — regla crítica), el producto desactivado no
aparece, y el flag `menu_qr` la gatea (404 sin él). Render HTML autocontenido (sin JS del dashboard).
"""
import uuid

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.menu_publico import MenuPublicoDeps, crear_router_menu_publico
from core.tenancy.context import ResolvedTenant


async def _seed_menu(engine, *, secreto: str) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, categoria, unidad_medida, precio_venta, "
                    "precio_compra, iva, permite_fraccion, activo) "
                    "VALUES ('Plato fuerte del día', 'Almuerzos', 'unidad', 19000, 9000, 0, false, true) "
                    "RETURNING id"
                )
            )
        ).scalar_one()
        grupo = (
            await s.execute(
                text(
                    "INSERT INTO modificador_grupos (producto_id, nombre, min_sel, max_sel, "
                    "obligatorio, orden, activo) VALUES (:p, 'Proteína', 1, 1, true, 0, true) RETURNING id"
                ),
                {"p": pid},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO modificador_opciones (grupo_id, nombre, delta_precio, activo) VALUES "
                "(:g, 'Carne asada', 0, true), (:g, 'Opción retirada', 0, false)"
            ),
            {"g": grupo},
        )
        # Producto desactivado + producto "secreto" del tenant (no debe filtrarse a otros).
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Plato retirado', 'unidad', 10000, 0, false, false), "
                f"       ('{secreto}', 'unidad', 14000, 0, false, true)"
            )
        )
        # Insumo de receta (BOM interno, ADR 0032 D9): activo y con inventario, pero es materia
        # prima del plato — JAMÁS debe salir en el menú público (fuga de datos internos).
        insumo = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, categoria, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo) "
                    "VALUES ('Arroz insumo (kg)', 'Insumos', 'kg', 1, 0, true, true) RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO recetas (producto_id, insumo_id, cantidad) VALUES (:p, :i, 0.2)"),
            {"p": pid, "i": insumo},
        )
        await s.commit()


def _app(tenants: dict, capacidades_por_tenant: dict) -> FastAPI:
    """App pública con puertos falsos: resolver por slug + capacidades por tenant."""

    class _Resolver:
        async def por_slug(self, slug: str) -> ResolvedTenant | None:
            return tenants.get(slug)

    async def _capacidades(tenant_id: int) -> frozenset[str]:
        return capacidades_por_tenant.get(tenant_id, frozenset())

    async def _whatsapp(tenant_id: int) -> str | None:
        return "573001112233"

    app = FastAPI()
    app.include_router(
        crear_router_menu_publico(
            MenuPublicoDeps(resolver=_Resolver(), capacidades=_capacidades, whatsapp=_whatsapp)
        )
    )
    return app


def _id_unico() -> int:
    """Id de tenant único por test: el engine-cache global se llavea por id — un id repetido entre
    tests apuntaría al engine de la base efímera ANTERIOR (ya destruida)."""
    return uuid.uuid4().int % 1_000_000_000


def _resolved(tenant, *, tenant_id: int, slug: str, nombre: str) -> ResolvedTenant:
    return ResolvedTenant(
        id=tenant_id, slug=slug, nombre=nombre, estado="activa",
        db_name=tenant.name, connection_url=tenant.url,
    )


async def test_menu_publico_sin_token_y_aislado(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    await _seed_menu(a.engine, secreto="Secreto De A")
    await _seed_menu(b.engine, secreto="Secreto De B")

    id_a, id_b = _id_unico(), _id_unico()
    tenants = {
        "resto-a": _resolved(a, tenant_id=id_a, slug="resto-a", nombre="Resto A"),
        "resto-b": _resolved(b, tenant_id=id_b, slug="resto-b", nombre="Resto B"),
    }
    caps = {id_a: frozenset({"menu_qr", "ventas"}), id_b: frozenset({"menu_qr", "ventas"})}
    app = _app(tenants, caps)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/publico/resto-a/menu")   # SIN token ni auth
        assert r.status_code == 200
        html = r.text
        # Contenido del tenant A, con modificadores activos y deep-link a WhatsApp.
        assert "Resto A" in html and "Plato fuerte del día" in html and "19.000" in html
        assert "Carne asada" in html and "Proteína" in html
        assert "wa.me/573001112233" in html
        # AISLAMIENTO: nada del tenant B ni campos internos.
        assert "Secreto De B" not in html and "Resto B" not in html
        for interno in ("precio_compra", "stock", "costo", "proveedor", "9000"):
            assert interno not in html, interno
        # Producto desactivado y opción retirada no aparecen.
        assert "Plato retirado" not in html and "Opción retirada" not in html
        # FUGA DE BOM (auditoría R0): un insumo de receta jamás aparece en el menú público,
        # aunque esté activo (es materia prima interna, no carta).
        assert "Arroz insumo" not in html and "Insumos" not in html
        # Sin JS del dashboard (página autocontenida).
        assert "<script" not in html.lower()

        # El menú de B es el de B.
        rb = await c.get("/publico/resto-b/menu")
        assert "Secreto De B" in rb.text and "Secreto De A" not in rb.text


async def test_menu_qr_autenticado_devuelve_url_y_svg():
    """Regresión auditoría R0: `GET /menu-qr` respondía 422 porque `Request` se importaba dentro
    de la factory y, con `from __future__ import annotations`, FastAPI no resolvía la anotación
    (trataba `request` como query param). El endpoint debe responder 200 con url + svg."""
    from apps.api.menu_publico import crear_router_menu_qr
    from core.auth.deps import get_current_user
    from core.auth.features import get_capacidades

    app = FastAPI()

    @app.middleware("http")
    async def _tenant_state(request, call_next):
        request.state.tenant = ResolvedTenant(
            id=1, slug="resto-a", nombre="Resto A", estado="activa",
            db_name="x", connection_url="postgresql://x/x",
        )
        return await call_next(request)

    app.include_router(crear_router_menu_qr())
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"menu_qr"})

    class _U:
        rol = "admin"

    app.dependency_overrides[get_current_user] = lambda: _U()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/menu-qr")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["url"].endswith("/publico/resto-a/menu")
        assert "<svg" in data["svg"]


async def test_menu_publico_gateado_por_flag_y_slug(tenant_factory):
    a = await tenant_factory()
    await _seed_menu(a.engine, secreto="Secreto")
    id_a = _id_unico()
    tenants = {"resto-a": _resolved(a, tenant_id=id_a, slug="resto-a", nombre="Resto A")}

    # Sin flag `menu_qr` → 404 (como si no existiera).
    app = _app(tenants, {id_a: frozenset({"ventas"})})
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        assert (await c.get("/publico/resto-a/menu")).status_code == 404
        # Slug inexistente → 404 sin tocar ninguna base.
        assert (await c.get("/publico/no-existe/menu")).status_code == 404
