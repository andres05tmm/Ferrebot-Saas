"""Menú digital público por tenant (F5 Pack Restaurante, ADR 0032 D6): `GET /publico/{slug}/menu`.

Página read-only SIN auth, autocontenida (HTML + CSS inline, sin JS del dashboard), pensada para el
QR impreso en la mesa. Orden NO negociable (tenancy.md §1): resolver la empresa por slug (control
DB) → validar el flag `menu_qr` → recién ahí abrir la base del tenant y leer SOLO catálogo activo
(nombre, precio, secciones, modificadores activos). JAMÁS expone costos, stock, proveedores ni
datos de otro tenant. Deep-link a WhatsApp para pedir.

Puertos inyectables (patrón `apps/tg_publico`): resolver por slug, capacidades y el número de
WhatsApp — los tests los falsean sin control DB.
"""
from __future__ import annotations

import html as _html
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal

# `Request` y `Principal` DEBEN importarse a nivel de módulo: con `from __future__ import
# annotations`, FastAPI resuelve las anotaciones contra los globals del módulo — importadas dentro
# de la factory no se resuelven y `request` degenera en query param obligatorio (422, bug R0).
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import control_session, tenant_session
from core.logging import get_logger
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_slug

log = get_logger("api.menu_publico")


class _ControlResolver:
    async def por_slug(self, slug: str) -> ResolvedTenant | None:
        async with control_session() as cs:
            return await resolve_tenant_by_slug(cs, slug)


async def _capacidades_control(tenant_id: int) -> frozenset[str]:
    async with control_session() as cs:
        return await ControlCapacidades(cs).efectivas(tenant_id)


async def _whatsapp_control(tenant_id: int) -> str | None:
    """Número de WhatsApp público del negocio (`config_empresa.whatsapp_publico`). None = sin botón."""
    async with control_session() as cs:
        valor = (
            await cs.execute(
                text(
                    "SELECT valor FROM config_empresa WHERE empresa_id = :e "
                    "AND clave = 'whatsapp_publico'"
                ),
                {"e": tenant_id},
            )
        ).scalar_one_or_none()
    valor = (valor or "").strip().lstrip("+")
    return valor or None


@dataclass(frozen=True, slots=True)
class MenuPublicoDeps:
    resolver: object = field(default_factory=_ControlResolver)
    capacidades: Callable[[int], Awaitable[frozenset[str]]] = _capacidades_control
    whatsapp: Callable[[int], Awaitable[str | None]] = _whatsapp_control


def _pesos(v) -> str:
    return "$" + f"{Decimal(v):,.0f}".replace(",", ".")


async def _leer_menu(session) -> list[dict]:
    """Catálogo ACTIVO para el público: secciones por categoría + modificadores activos. Solo lo
    que un comensal puede ver — nada de costos/stock/proveedores (por construcción: no se leen)."""
    # Un producto que es INSUMO de una receta (BOM, ADR 0032 D9) es materia prima interna, no
    # carta: excluirlo aunque esté activo (auditoría R0: fuga de "Arroz insumo (kg)" al comensal).
    productos = (
        await session.execute(
            text(
                "SELECT id, nombre, categoria, precio_venta FROM productos "
                "WHERE activo AND id NOT IN (SELECT insumo_id FROM recetas) "
                "ORDER BY categoria NULLS LAST, nombre"
            )
        )
    ).all()
    ids = [p.id for p in productos]
    grupos: dict[int, list[dict]] = {}
    if ids:
        filas = (
            await session.execute(
                text(
                    "SELECT g.producto_id, g.nombre AS grupo, g.min_sel, g.max_sel, "
                    "       o.nombre AS opcion, o.delta_precio "
                    "FROM modificador_grupos g JOIN modificador_opciones o ON o.grupo_id = g.id "
                    "WHERE g.activo AND o.activo AND g.producto_id = ANY(:ids) "
                    "ORDER BY g.orden, g.id, o.id"
                ),
                {"ids": ids},
            )
        ).all()
        for f in filas:
            por_producto = grupos.setdefault(f.producto_id, [])
            grupo = next((g for g in por_producto if g["nombre"] == f.grupo), None)
            if grupo is None:
                grupo = {"nombre": f.grupo, "opciones": []}
                por_producto.append(grupo)
            grupo["opciones"].append({"nombre": f.opcion, "delta": Decimal(f.delta_precio)})

    secciones: list[dict] = []
    for p in productos:
        nombre_seccion = p.categoria or "Menú"
        seccion = next((s for s in secciones if s["nombre"] == nombre_seccion), None)
        if seccion is None:
            seccion = {"nombre": nombre_seccion, "productos": []}
            secciones.append(seccion)
        seccion["productos"].append({
            "nombre": p.nombre, "precio": Decimal(p.precio_venta),
            "grupos": grupos.get(p.id, []),
        })
    return secciones


def _render(nombre: str, secciones: list[dict], whatsapp: str | None) -> str:
    """HTML autocontenido (inline CSS, cero JS). Todo texto va escapado."""
    e = _html.escape
    partes = [
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(nombre)} — Menú</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:0;background:#faf7f2;color:#222}"
        "header{background:#C8200E;color:#fff;padding:20px 16px;text-align:center}"
        "h1{margin:0;font-size:1.4rem}main{max-width:640px;margin:0 auto;padding:16px}"
        "h2{font-size:1.05rem;border-bottom:2px solid #C8200E;padding-bottom:4px;margin:20px 0 8px}"
        ".p{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-bottom:1px dashed #ddd}"
        ".mods{font-size:.85rem;color:#555;margin:2px 0 6px 12px}"
        ".precio{white-space:nowrap;font-weight:600}"
        ".wa{display:block;text-align:center;background:#25D366;color:#fff;text-decoration:none;"
        "padding:12px;border-radius:10px;font-weight:700;margin:24px 0}</style></head><body>",
        f"<header><h1>{e(nombre)}</h1><div>Menú</div></header><main>",
    ]
    for seccion in secciones:
        partes.append(f"<h2>{e(seccion['nombre'])}</h2>")
        for p in seccion["productos"]:
            partes.append(
                f"<div class='p'><span>{e(p['nombre'])}</span>"
                f"<span class='precio'>{_pesos(p['precio'])}</span></div>"
            )
            for g in p["grupos"]:
                opciones = " · ".join(
                    e(o["nombre"]) + (f" (+{_pesos(o['delta'])})" if o["delta"] else "")
                    for o in g["opciones"]
                )
                partes.append(f"<div class='mods'><b>{e(g['nombre'])}:</b> {opciones}</div>")
    if whatsapp:
        partes.append(
            f"<a class='wa' href='https://wa.me/{e(whatsapp)}?text=Hola!%20Quiero%20pedir'>"
            "Pedir por WhatsApp</a>"
        )
    partes.append("</main></body></html>")
    return "".join(partes)


def crear_router_menu_qr() -> APIRouter:
    """Router AUTENTICADO del QR (dashboard): la URL pública del menú del tenant + su QR en SVG.

    Gateado por `menu_qr` (404 sin el flag) y rol vendedor+. El QR se genera con `segno`
    (pure-python) sobre la URL pública derivada del host del request (subdominio del tenant).
    """
    router = APIRouter(
        prefix="/menu-qr", tags=["menu-qr"],
        dependencies=[Depends(require_feature("menu_qr"))],
    )

    @router.get("")
    async def menu_qr(
        request: Request,
        _user: Principal = Depends(require_role("vendedor")),
    ) -> dict:
        import segno

        tenant = getattr(request.state, "tenant", None)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Empresa no resuelta")
        base = str(request.base_url).rstrip("/")
        url = f"{base}/publico/{tenant.slug}/menu"
        svg = segno.make(url, error="m").svg_inline(scale=6, dark="#111")
        return {"url": url, "svg": svg}

    return router


def crear_router_menu_publico(deps: MenuPublicoDeps | None = None) -> APIRouter:
    deps = deps or MenuPublicoDeps()
    router = APIRouter()

    @router.get("/publico/{slug}/menu", response_class=HTMLResponse)
    async def menu_publico(slug: str) -> HTMLResponse:
        tenant = await deps.resolver.por_slug(slug)
        if tenant is None or tenant.estado != "activa":
            return HTMLResponse("No encontrado", status_code=404)
        capacidades = await deps.capacidades(tenant.id)
        if "menu_qr" not in capacidades:
            return HTMLResponse("No encontrado", status_code=404)
        # `tenant_session` es un generador async (estilo dependencia): se envuelve per-call.
        async with asynccontextmanager(tenant_session)(tenant) as session:
            secciones = await _leer_menu(session)
        numero = await deps.whatsapp(tenant.id)
        return HTMLResponse(_render(tenant.nombre, secciones, numero))

    return router
