"""Esquema Pydantic v2 del manifiesto de tenant (ADR 0007).

Tipa 1:1 el ejemplo `tools/onboarding/clinica-demo.manifest.example.yaml`. Los campos cuya regla de
negocio es más rica que "existe y tiene tipo" (formato de franja, día 0..6, tipo de recurso del enum,
`presta` -> servicio declarado, features del catálogo) se dejan PERMISIVOS aquí (str/int) y los
verifica `validacion.validar`, que reúne todos los errores en un mensaje claro. El esquema solo
garantiza forma y tipos; la semántica de pack la pone la validación.

`packs.agenda.config` mapea 1:1 a las columnas de `agenda_config` (modules/agenda/models.py); los
defaults espejan los `server_default` del esquema para que omitir un campo en el YAML produzca el
mismo valor que tendría la columna.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.tenancy.resolver import LABELS_RESERVADOS

# Patrón ESTRICTO del slug (ADR 0010 §Guardarraíles v1): el slug se vuelve nombre de base
# (`CREATE DATABASE "ferrebot_<slug>"`), así que un slug no validado es un vector de inyección de
# identificador. minúscula inicial + [a-z0-9-], 2..41 chars. El job lo RE-valida antes de tocar la BD.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"
_SLUG_RE = re.compile(SLUG_PATTERN)


def slug_valido(slug: str) -> bool:
    """True si `slug` cumple el patrón estricto y no es un label reservado del resolver.

    Defensa en profundidad del job (además del esquema). Un slug reservado (`app`, `api`, …) sería
    inalcanzable por subdominio: el resolver trata esos labels como "sin subdominio".
    """
    return isinstance(slug, str) and bool(_SLUG_RE.match(slug)) and slug not in LABELS_RESERVADOS


# "HH:MM" 00:00..23:59 — una hora suelta (check-in/out de reservas, horario de cocina de pedidos).
# Espeja el `_FRANJA` de `validacion` pero para un solo extremo; se valida en el esquema (es un valor
# escalar, no una lista: el error sale al parsear, con mensaje claro).
_HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _hora_valida(valor: str) -> str:
    if not _HORA_RE.match(valor):
        raise ValueError(f"hora mal formada '{valor}' (esperado \"HH:MM\")")
    return valor


# Clave natural del pack POS (ADR 0011 §D3): dos nombres que solo difieren en mayúsculas o espacios
# son el MISMO producto. La usan el loader (upsert) y la validación (nombres duplicados); el loader la
# espeja en SQL (`lower(btrim(regexp_replace(nombre,'\s+',' ','g')))`) para encontrar la fila que insertó.
_ESPACIOS_RE = re.compile(r"\s+")


def normalizar_nombre(nombre: str) -> str:
    """Forma canónica de un nombre de producto: minúsculas, sin espacios de borde, internos colapsados."""
    return _ESPACIOS_RE.sub(" ", nombre).strip().lower()


class _Base(BaseModel):
    # Falla cerrado: un campo no modelado (típico typo) es un error, no se ignora en silencio.
    model_config = ConfigDict(extra="forbid")


class Identidad(_Base):
    # Patrón estricto: el slug se materializa en `CREATE DATABASE "ferrebot_<slug>"` (ver SLUG_PATTERN).
    slug: str = Field(pattern=SLUG_PATTERN)
    nombre: str
    # Requerido: empresas.nit es NOT NULL + UNIQUE en el control DB; un NIT ausente debe fallar como
    # error de validación limpio (Fase 1), no como violación NOT NULL al insertar (Fase 3).
    nit: str

    @field_validator("slug")
    @classmethod
    def _slug_no_reservado(cls, v: str) -> str:
        # El slug es también el subdominio del tenant ({slug}.BASE_DOMAIN): un label reservado del
        # resolver nunca resolvería a este tenant y chocaría con la entrada de clientes (app./api.).
        if v in LABELS_RESERVADOS:
            reservados = ", ".join(sorted(LABELS_RESERVADOS))
            raise ValueError(
                f"identidad.slug '{v}' es un label reservado de la plataforma ({reservados}): "
                "es el subdominio de entrada, no puede ser un tenant"
            )
        return v


def _email_normalizado(v: str | None, *, rotulo: str) -> str | None:
    """Valida y normaliza un email del manifiesto (trim). None pasa (opcional)."""
    if v is None:
        return None
    v = v.strip()
    if "@" not in v or "." not in v.split("@")[-1]:
        raise ValueError(f"{rotulo} no parece un email válido")
    return v


class Admin(_Base):
    nombre: str = "Admin"
    telegram_id: int | None = None
    # Email del admin para el login real (ADR 0009): el provisionador crea su `identidad` y emite un
    # enlace de set-password. Opcional pero RECOMENDADO. NUNCA una contraseña en el manifiesto.
    email: str | None = None

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v: str | None) -> str | None:
        return _email_normalizado(v, rotulo="admin.email")


class IdentidadExtra(_Base):
    """Identidad de login ADICIONAL a la del admin (login real, ADR 0009). El provisionador crea su
    `usuario` en la base del tenant (con el `rol` dado) + su `identidad` en el control DB y emite un
    enlace de set-password. Caso de uso: la identidad DEMO de un tenant demo (rol `vendedor`, para que
    un prospecto pruebe el dashboard sin poder romper la demo). NUNCA una contraseña en el manifiesto.
    """

    email: str
    nombre: str = "Demo"
    # rol del tenant: enum usuario_rol = (admin, vendedor). Default vendedor (no-admin) a propósito.
    rol: Literal["admin", "vendedor"] = "vendedor"

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v: str) -> str:
        validado = _email_normalizado(v, rotulo="identidad.email")
        assert validado is not None  # email es requerido aquí (no Optional)
        return validado


class Plan(_Base):
    nombre: str = "Custom"
    features: list[str] = Field(default_factory=list)


class Branding(_Base):
    # Preset de marca por vertical (plan §5.2): el tenant nace con el look de su gremio. Validado
    # contra el registro de `core.tenancy.branding_presets`. None → default de plataforma (melquiadez).
    preset: str | None = None
    # Override puntual del acento: si se da, GANA sobre el primario del preset (Punto Rojo conserva su
    # rojo). None (lo normal en un tenant con preset) → el primario lo pone el preset.
    color_primario: str | None = None
    nombre_comercial: str | None = None
    logo_url: str | None = None
    dominio: str | None = None
    # `tema`: nombre VIEJO del preset (compat). Si no hay `preset`, `leer_branding` lo usa de fallback.
    tema: str | None = None

    @field_validator("preset")
    @classmethod
    def _preset_valido(cls, v: str | None) -> str | None:
        # Import local: el esquema no debe arrastrar core.tenancy al importarse (evita ciclos).
        from core.tenancy.branding_presets import PRESETS, es_preset_valido

        if v is not None and not es_preset_valido(v):
            raise ValueError(
                f"branding.preset '{v}' no existe; presets válidos: {', '.join(sorted(PRESETS))}"
            )
        return v


# ---------------------------------------------------------------------------
# Pack Agenda
# ---------------------------------------------------------------------------
class AgendaConfig(_Base):
    """1:1 con la tabla `agenda_config` (una fila). Defaults = `server_default` del esquema."""

    zona_horaria: str = "America/Bogota"
    intervalo_slots_min: int = 15
    anticipacion_minima_min: int = 120
    ventana_maxima_dias: int = 30
    politica_cancelacion_horas: int = 24
    corte_riesgo_horas: int = 2
    permite_reagendar: bool = True
    modo_confirmacion: Literal["auto", "manual"] = "auto"
    requiere_anticipo: bool = False
    anticipo_tipo: Literal["porcentaje", "fijo"] | None = None
    anticipo_valor: int | None = None
    capacidad_por_slot: int = 1
    recordatorios_horas: list[int] = Field(default_factory=lambda: [24, 2])
    persona: str | None = None
    google_calendar_id: str | None = None
    # Modo reservas/noches (migración tenant 0022): las horas que convierten "N noches" en
    # [check-in, check-out). Defaults = `server_default` del esquema. Solo importan en hoteles, pero
    # toda agenda las tiene (columnas NOT NULL), así que el manifiesto puede fijarlas en cualquier vertical.
    checkin_hora: str = "15:00"
    checkout_hora: str = "12:00"

    @field_validator("checkin_hora", "checkout_hora")
    @classmethod
    def _hora_hhmm(cls, v: str) -> str:
        return _hora_valida(v)


class Servicio(_Base):
    """-> tabla `servicios`. `precio` en pesos (entero); buffers en minutos."""

    nombre: str
    duracion_min: int
    precio: int | None = None
    buffer_antes_min: int = 0
    buffer_despues_min: int = 0
    categoria: str | None = None
    descripcion: str | None = None


class Disponibilidad(_Base):
    """Horario semanal: `dias` (0=lunes…6=domingo) y `franjas` "HH:MM-HH:MM" (varias = mañana/tarde).

    Tipos permisivos a propósito: el rango de `dias` y el formato de `franjas` los valida
    `validacion.validar` con mensajes claros (ver módulo).
    """

    dias: list[int]
    franjas: list[str]


class Recurso(_Base):
    """-> tablas `recursos` + `recurso_servicio` + `disponibilidad`.

    `tipo` se valida contra el enum `recurso_tipo` y `presta` contra los servicios declarados en
    `validacion.validar` (no aquí), para reunir todos los errores del manifiesto en un solo mensaje.
    """

    nombre: str
    tipo: str
    presta: list[str] = Field(default_factory=list)
    disponibilidad: list[Disponibilidad] = Field(default_factory=list)


class PackAgenda(_Base):
    config: AgendaConfig = Field(default_factory=AgendaConfig)
    servicios: list[Servicio] = Field(default_factory=list)
    recursos: list[Recurso] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pack FAQ
# ---------------------------------------------------------------------------
class EntradaFaq(_Base):
    """-> tabla `conocimiento` (titulo, contenido, orden, activo)."""

    titulo: str
    contenido: str
    orden: int = 0


class PackFaq(_Base):
    entradas: list[EntradaFaq] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pack Pedidos (ADR 0016) — config de cocina/domicilios. El MENÚ no vive aquí: es el catálogo del POS
# (`packs.pos.productos`), que el pack de pedidos solo LEE. Aquí solo la operación: horario, mínimo,
# tiempo estimado y zonas de domicilio.
# ---------------------------------------------------------------------------
class ZonaDomicilio(_Base):
    """-> tabla `zonas_domicilio` (barrio → tarifa). `tarifa` en pesos (entero); el loader → Decimal."""

    nombre: str
    tarifa: int


class PedidoConfig(_Base):
    """-> tabla `pedido_config` (una sola fila). Defaults = `server_default` del esquema (0019)."""

    activo: bool = True
    hora_apertura: str = "08:00"
    hora_cierre: str = "21:00"
    minimo_pedido: int = 0          # pesos (entero)
    tiempo_estimado_min: int = 45
    costo_domicilio_default: int = 0  # pesos (entero)

    @field_validator("hora_apertura", "hora_cierre")
    @classmethod
    def _hora_hhmm(cls, v: str) -> str:
        return _hora_valida(v)


class PackPedidos(_Base):
    config: PedidoConfig = Field(default_factory=PedidoConfig)
    zonas: list[ZonaDomicilio] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pack POS (retail: catálogo declarativo) — ADR 0011 §D3
# ---------------------------------------------------------------------------
class FraccionPos(_Base):
    """Fracción de venta -> tabla `productos_fracciones` (modules/inventario/models.py).

    `decimal` es el equivalente en unidades de la fracción (p. ej. galón→1/4 = 0.25); QTY
    Numeric(12,3). `precio_total` (entero pesos) es lo que se cobra por la fracción; `precio_unitario`
    (entero pesos), si se da, es el precio por unidad equivalente. La coherencia aritmética
    decimal×precio_unitario ≈ precio_total la verifica `validacion.validar` (no aquí).
    """

    fraccion: str
    decimal: float | None = None
    precio_total: int
    precio_unitario: int | None = None


class EscalonadoPos(_Base):
    """Precio escalonado por cantidad (modelo FerreBot) -> columnas `precio_umbral`/`precio_bajo_umbral`/
    `precio_sobre_umbral` de `productos`.

    `umbral` es la CANTIDAD a partir de la cual cambia el precio (QTY Numeric(12,3)); `bajo` y `sobre`
    (enteros pesos) son los precios por unidad por debajo y a partir del umbral. Los tres juntos o
    ninguno (Pydantic exige los tres si la sección existe; `validacion` verifica que sean > 0).
    """

    umbral: float
    bajo: int
    sobre: int


class ProductoPos(_Base):
    """-> tabla `productos`. Precios en pesos (enteros); el loader castea a Decimal (MONEY).

    Clave natural para el upsert idempotente del loader: `codigo` si está, si no `nombre` normalizado
    (lower/trim/colapso de espacios). Tipos permisivos: las reglas ricas (precio>0, iva ∈ {0,5,19},
    fracciones solo si `permite_fraccion`, coherencia de fracción/escalonado) van en `validacion.py`.
    """

    codigo: str | None = None
    nombre: str
    categoria: str | None = None
    unidad_medida: str
    precio_venta: int
    iva: int = 19
    permite_fraccion: bool = False
    precio_compra: int | None = None
    escalonado: EscalonadoPos | None = None
    fracciones: list[FraccionPos] = Field(default_factory=list)
    # Stock de apertura: el loader crea la fila de inventario CON su movimiento ENTRADA (regla 7 de
    # CLAUDE.md: nada toca stock sin movimiento). Ausente = no se siembra inventario.
    stock_inicial: float | None = None


class AliasPos(_Base):
    """-> tabla `aliases` (variante/typo → forma canónica). `producto`, si se da, es el NOMBRE de un
    producto declarado en `productos[]`; el loader lo resuelve a `producto_id`. La existencia del
    producto referido la valida `validacion.validar`."""

    termino: str
    reemplazo: str
    producto: str | None = None


class PackPos(_Base):
    productos: list[ProductoPos] = Field(default_factory=list)
    aliases: list[AliasPos] = Field(default_factory=list)


class Packs(_Base):
    agenda: PackAgenda | None = None
    faq: PackFaq | None = None
    pos: PackPos | None = None
    pedidos: PackPedidos | None = None


# ---------------------------------------------------------------------------
# Canal
# ---------------------------------------------------------------------------
class CanalWhatsapp(_Base):
    """-> tabla `wa_numeros`. `phone_number_id` lo da Kapso (no es secreto)."""

    phone_number_id: str
    numero: str | None = None
    waba_id: str | None = None


class Canal(_Base):
    whatsapp: CanalWhatsapp | None = None


# ---------------------------------------------------------------------------
# Raíz
# ---------------------------------------------------------------------------
class Manifiesto(_Base):
    version: int = 1
    identidad: Identidad
    admin: Admin = Field(default_factory=Admin)
    # Identidades de login ADICIONALES a la del admin (p. ej. la identidad demo, rol vendedor).
    identidades: list[IdentidadExtra] = Field(default_factory=list)
    plan: Plan | None = None
    features_override: dict[str, bool] = Field(default_factory=dict)
    branding: Branding = Field(default_factory=Branding)
    secretos: dict[str, object] = Field(default_factory=dict)
    config: dict[str, object] = Field(default_factory=dict)
    packs: Packs = Field(default_factory=Packs)
    canal: Canal = Field(default_factory=Canal)
