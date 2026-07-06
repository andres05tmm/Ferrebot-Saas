"""Router de arranque del dashboard: GET /config (api-contract.md).

Devuelve `{ features, branding, usuario }` para que el dashboard se configure al cargar. Es el
bootstrap: NO lleva feature-gate (rol mínimo = cualquier usuario autenticado). Las capacidades vienen
cacheadas (`get_capacidades`) y el branding del control DB; ambas deps son inyectables para test.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import control_session
from core.tenancy.catalogo import capacidades_completas
from core.tenancy.config_empresa import cargar_auto_facturar_venta
from core.tenancy.control_repo import leer_branding

router = APIRouter(tags=["config"])


class Branding(BaseModel):
    """Marca blanca de la empresa (control DB); `color_primario` siempre presente (default de marca).

    El branding viaja YA RESUELTO (preset + overrides): el front recibe `tokens` planos —no el nombre
    del preset a interpretar— y los aplica como variables CSS. `color_primario`/`tema`/`preset` se
    mantienen por compatibilidad y para el `data-tema` con nombre. `tokens` vacío → el front cae a su
    fallback (solo `--color-primary`), así un /config viejo no rompe nada.
    """

    logo_url: str | None = None
    color_primario: str
    nombre_comercial: str | None = None
    dominio: str | None = None
    # Tema de UI con nombre (p. ej. "aurora"); None → el dashboard usa el tema base. El front lo aplica
    # como `data-tema` en <html> (bloque de CSS vars), combinable con light/dark.
    tema: str | None = None
    # Preset de marca por vertical (plan §5.2). El front lo usa como `data-tema` y como llave de caché.
    preset: str | None = None
    # Tokens RESUELTOS (paleta + radio + fuentes); ver core.tenancy.branding_presets.TOKEN_KEYS.
    tokens: dict[str, str] = {}


class Usuario(BaseModel):
    """Identidad del usuario autenticado, derivada del `Principal` del token."""

    id: int
    rol: str
    tenant: str


class ConfigLeer(BaseModel):
    """Respuesta de GET /config: capacidades activas, branding, usuario y preferencias de UI.

    `facturar_en_venta` (config del tenant, default True) le dice al POS si debe auto-facturar cada
    venta: cuando es False, Ventas Rápidas ofrece "Sin factura" (venta interna, factura a pedido)."""

    features: list[str]
    branding: Branding
    usuario: Usuario
    facturar_en_venta: bool = True


async def get_branding(request: Request) -> Branding:
    """Lee el branding de la empresa del request desde el control DB (defaults si no hay fila)."""
    async with control_session() as cs:
        datos = await leer_branding(cs, request.state.tenant.id)
    return Branding(**datos)


async def get_facturar_en_venta(request: Request) -> bool:
    """Toggle `facturar_en_venta` del tenant (control DB, default True). Lo consume el POS del dashboard."""
    async with control_session() as cs:
        return await cargar_auto_facturar_venta(cs, request.state.tenant.id)


async def get_branding_opcional(request: Request) -> Branding | None:
    """Branding SI hay tenant resuelto, None si no (host sin empresa). Para endpoints públicos como el
    manifest PWA, que se piden antes del login y deben tolerar la ausencia de empresa."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        return None
    async with control_session() as cs:
        datos = await leer_branding(cs, tenant.id)
    return Branding(**datos)


# Íconos del manifest. El PRIMERO es el SVG dinámico teñido con el color del tenant (Punto Rojo instala
# rojo); los PNG estáticos (marca Melquiadez neutra) quedan de fallback + maskable, garantizando la
# instalabilidad donde el navegador no honre el SVG. iOS usa el apple-touch-icon (PNG) del index.html.
_ICONS = [
    {"src": "/api/v1/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    {"src": "/pwa-192x192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/pwa-512x512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/maskable-icon-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
]

# Defaults neutros si no hay tenant resuelto (host sin empresa) o sin branding.
_NOMBRE_DEFAULT = "FerreBot"
_COLOR_DEFAULT = "#C8200E"


def _icono_svg(color: str) -> str:
    """Ícono del lanzador como SVG: sello M en crema sobre el color primario del tenant.

    Escalable (el navegador lo rasteriza al tamaño que necesite). Colores CONCRETOS (sin currentColor):
    fondo = color del tenant; tinta = crema; chispa = crema cálida (legible sobre rojo/oscuro)."""
    ink, chispa = "#f9f7f3", "#f4dca8"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        f'<rect width="512" height="512" fill="{color}"/>'
        '<g transform="translate(74 74) scale(0.71)" fill="none" '
        f'stroke="{ink}" stroke-linecap="round">'
        '<path stroke-width="11" d="M 451.7 143 A 226 226 0 1 1 303 34.9"/>'
        '<path stroke-width="9.5" d="M 138 210 C 148 194 160 180 174 170"/>'
        '<path stroke-width="23" d="M 174 169 C 166 240 157 315 145 368"/>'
        '<path stroke-width="32" d="M 175 166 C 200 232 233 305 259 354"/>'
        '<path stroke-width="9" d="M 259 358 C 286 296 315 230 342 168"/>'
        '<path stroke-width="25" d="M 343 167 C 350 224 354 270 355 310"/>'
        '</g>'
        f'<path transform="translate(74 74) scale(0.71)" fill="{ink}" d="M 340 330 '
        'C 388 334 428 296 444 240 C 458 192 452 128 428 86 C 440 140 436 196 419 240 '
        'C 403 284 374 306 348 308 Z"/>'
        f'<circle transform="translate(74 74) scale(0.71)" cx="256" cy="482" r="7" fill="{chispa}"/>'
        '</svg>'
    )


@router.get("/icon.svg", include_in_schema=False)
async def icon_pwa(branding: Branding | None = Depends(get_branding_opcional)):
    """Ícono PWA por-tenant (PÚBLICO): SVG teñido con el color primario de la empresa. Fallback al rojo
    de marca sin tenant. Referenciado por el manifest como ícono principal."""
    from fastapi.responses import Response

    color = (branding.color_primario if branding else None) or _COLOR_DEFAULT
    return Response(
        _icono_svg(color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest_pwa(branding: Branding | None = Depends(get_branding_opcional)) -> JSONResponse:
    """Manifest PWA por-tenant (PÚBLICO, sin auth): el navegador lo pide antes del login.

    Resuelve la empresa por subdominio (TenantMiddleware) y arma el manifest con su marca: instala como
    'Punto Rojo' con su rojo. Sin tenant resuelto cae a defaults neutros (no rompe la instalación). Solo
    expone branding público (nombre, color); nada de datos de negocio.
    """
    nombre = (branding.nombre_comercial if branding else None) or _NOMBRE_DEFAULT
    color = (branding.color_primario if branding else None) or _COLOR_DEFAULT

    cuerpo = {
        "name": nombre,
        "short_name": nombre[:12],
        "description": f"Panel de {nombre}",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "lang": "es-CO",
        "dir": "ltr",
        "background_color": "#f7f4ee",
        "theme_color": color,
        "icons": _ICONS,
    }
    return JSONResponse(
        cuerpo,
        media_type="application/manifest+json",
        # Cambia poco pero debe reflejar rebranding pronto: revalidación barata.
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/config", response_model=ConfigLeer)
async def obtener_config(
    user: Principal = Depends(get_current_user),
    capacidades: frozenset[str] = Depends(get_capacidades),
    branding: Branding = Depends(get_branding),
    facturar_en_venta: bool = Depends(get_facturar_en_venta),
) -> ConfigLeer:
    """Arranque del dashboard: núcleo ∪ efectivas (ordenado), branding, usuario y preferencias de UI."""
    return ConfigLeer(
        features=sorted(capacidades_completas(capacidades)),
        branding=branding,
        usuario=Usuario(id=user.user_id, rol=user.rol, tenant=user.tenant),
        facturar_en_venta=facturar_en_venta,
    )
