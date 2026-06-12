"""Presets de branding por vertical (plan superficie pública §5.2): fuente única de datos.

Cada propuesta de `design-propuestas/` se vuelve un PRESET: un set de tokens (paleta + radio + fuentes)
extraído de su `:root` de CSS. Un tenant declara `branding.preset` en el manifiesto y nace con el look
de su gremio sin diseñar nada. El default de plataforma es `melquiadez` (oro viejo/tinta, plan §1) —
sustituye al viejo `#C8200E`, que ahora es branding EXPLÍCITO de Punto Rojo.

Módulo PURO (sin IO ni BD): lo consume `control_repo.leer_branding` (resuelve preset+overrides), GET
/config (entrega los tokens planos) y la validación del manifiesto. El dashboard recibe tokens YA
resueltos y solo los aplica como variables CSS (no interpreta el nombre del preset).

Tokens (claves estables; el front las mapea a `--color-*`/`--radius-brand`/`--font-*`):
- primario, primario_up : acento de marca y su variante realzada (hover/gradiente).
- superficie, card, linea: fondo de la app, tarjetas y bordes.
- tinta, tinta_suave     : texto principal y secundario.
- ok, warn, bad          : estados (éxito/aviso/error).
- radius                 : radio base.
- font_display, font_ui  : tipografía de titulares y de cuerpo (familias de Google Fonts).
"""
from __future__ import annotations

from dataclasses import dataclass

# Orden canónico de los tokens de un preset (lo verifica el test y lo recorre la resolución).
TOKEN_KEYS: tuple[str, ...] = (
    "primario", "primario_up", "superficie", "card", "linea",
    "tinta", "tinta_suave", "ok", "warn", "bad",
    "radius", "font_display", "font_ui",
)


@dataclass(frozen=True, slots=True)
class BrandingPreset:
    """Tokens de un vertical, extraídos del `:root` de su propuesta HTML. Inmutable."""

    primario: str
    primario_up: str
    superficie: str
    card: str
    linea: str
    tinta: str
    tinta_suave: str
    ok: str
    warn: str
    bad: str
    radius: str
    font_display: str
    font_ui: str

    def tokens(self) -> dict[str, str]:
        """Dict plano `{clave: valor}` en el orden de `TOKEN_KEYS` (lo que viaja a /config)."""
        return {k: getattr(self, k) for k in TOKEN_KEYS}


# Cada preset = el `:root` de su `design-propuestas/propuesta-*.html` (12 jun 2026). El `primario_up`
# es la variante realzada del acento (en `navaja` existe explícito como `--brand-up`; en el resto se
# toma una versión más clara del primario, coherente con la propuesta).
PRESETS: dict[str, BrandingPreset] = {
    # Clínica dental Aurora — teal clínico, claro.
    "aurora": BrandingPreset(
        primario="#0e8784", primario_up="#14a39f", superficie="#f6f9f9", card="#ffffff",
        linea="#e3edec", tinta="#1f2d2c", tinta_suave="#5f7472",
        ok="#2e9e6b", warn="#e6a23c", bad="#d9534f",
        radius="16px", font_display="Nunito", font_ui="Inter",
    ),
    # Restaurante Brasa — ladrillo/brasa cálido, claro.
    "brasa": BrandingPreset(
        primario="#d6452c", primario_up="#e85d42", superficie="#faf5ef", card="#ffffff",
        linea="#eee0d8", tinta="#2b201d", tinta_suave="#7d6b66",
        ok="#2e9e6b", warn="#e6a23c", bad="#d9534f",
        radius="16px", font_display="Figtree", font_ui="Figtree",
    ),
    # Barbería El Patio — oro sobre carbón, OSCURO (la propuesta nace en dark).
    "navaja": BrandingPreset(
        primario="#d99a3d", primario_up="#e8b066", superficie="#171310", card="#211c17",
        linea="#352d24", tinta="#f0e9df", tinta_suave="#a59a8a",
        ok="#7fb069", warn="#e6a23c", bad="#d9534f",
        radius="14px", font_display="Archivo", font_ui="Archivo",
    ),
    # Hotel Brisa — mar profundo + arena, claro (primario = océano; acento dorado vive en la propuesta).
    "brisa": BrandingPreset(
        primario="#0b3954", primario_up="#155b7d", superficie="#f7f1e5", card="#fffdf8",
        linea="#e8dfcd", tinta="#2a2f33", tinta_suave="#7d8489",
        ok="#2f8f6b", warn="#e6a23c", bad="#e07a5f",
        radius="14px", font_display="Cormorant Garamond", font_ui="Jost",
    ),
    # Genérico Lienzo — violeta neutro, claro (para verticales sin preset propio).
    "lienzo": BrandingPreset(
        primario="#6c5ce7", primario_up="#8674f0", superficie="#f4f5f9", card="#ffffff",
        linea="#e6e8ef", tinta="#16181d", tinta_suave="#6b7280",
        ok="#16a34a", warn="#d97706", bad="#dc2626",
        radius="14px", font_display="Sora", font_ui="Inter",
    ),
    # Melquiadez — DEFAULT de plataforma (plan §1): papel cálido, tinta noche, oro viejo de acento.
    "melquiadez": BrandingPreset(
        primario="#b8924f", primario_up="#cda863", superficie="#f7f4ee", card="#fffdf9",
        linea="#e7e0d3", tinta="#211b16", tinta_suave="#6a6052",
        ok="#2e9e6b", warn="#c2410c", bad="#b91c1c",
        radius="14px", font_display="Fraunces", font_ui="Bricolage Grotesque",
    ),
}

# Default de plataforma: un tenant sin preset (o con uno desconocido) hereda Melquiadez.
DEFAULT_PRESET = "melquiadez"


def es_preset_valido(nombre: str | None) -> bool:
    """True si `nombre` es un preset registrado (validación del manifiesto)."""
    return isinstance(nombre, str) and nombre in PRESETS


# Campos legacy de la fila `branding` que pasan sin transformar a la respuesta (overrides puntuales).
_LEGACY_KEYS = ("logo_url", "nombre_comercial", "dominio")


def resolver_branding(fila: dict | None) -> dict:
    """Resuelve la fila `branding` (o None) a `{preset, tokens, color_primario, ...legacy}`.

    Parte de los tokens del preset (default `melquiadez`). Un `color_primario` explícito en la fila
    GANA sobre el primario del preset (Punto Rojo conserva su rojo) y arrastra el `primario_up` para no
    romper el contraste del hover. El resto de tokens siguen siendo del preset (no se inventan). El
    front recibe `tokens` planos + `color_primario` (compat con el theming actual).
    """
    fila = fila or {}
    nombre = fila.get("preset")
    preset = PRESETS[nombre] if es_preset_valido(nombre) else PRESETS[DEFAULT_PRESET]
    nombre_resuelto = nombre if es_preset_valido(nombre) else DEFAULT_PRESET

    tokens = preset.tokens()
    override = fila.get("color_primario")
    if override:
        tokens["primario"] = override
        tokens["primario_up"] = override

    resultado: dict = {"preset": nombre_resuelto, "tokens": tokens}
    resultado["color_primario"] = tokens["primario"]
    for clave in _LEGACY_KEYS:
        resultado[clave] = fila.get(clave)
    return resultado
