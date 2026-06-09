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

# Patrón ESTRICTO del slug (ADR 0010 §Guardarraíles v1): el slug se vuelve nombre de base
# (`CREATE DATABASE "ferrebot_<slug>"`), así que un slug no validado es un vector de inyección de
# identificador. minúscula inicial + [a-z0-9-], 2..41 chars. El job lo RE-valida antes de tocar la BD.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,40}$"
_SLUG_RE = re.compile(SLUG_PATTERN)


def slug_valido(slug: str) -> bool:
    """True si `slug` cumple el patrón estricto. Defensa en profundidad del job (además del esquema)."""
    return isinstance(slug, str) and bool(_SLUG_RE.match(slug))


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


class Admin(_Base):
    nombre: str = "Admin"
    telegram_id: int | None = None
    # Email del admin para el login real (ADR 0009): el provisionador crea su `identidad` y emite un
    # enlace de set-password. Opcional pero RECOMENDADO. NUNCA una contraseña en el manifiesto.
    email: str | None = None

    @field_validator("email")
    @classmethod
    def _email_valido(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("admin.email no parece un email válido")
        return v


class Plan(_Base):
    nombre: str = "Custom"
    features: list[str] = Field(default_factory=list)


class Branding(_Base):
    color_primario: str = "#C8200E"
    nombre_comercial: str | None = None
    logo_url: str | None = None
    dominio: str | None = None


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


class Packs(_Base):
    agenda: PackAgenda | None = None
    faq: PackFaq | None = None


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
    plan: Plan | None = None
    features_override: dict[str, bool] = Field(default_factory=dict)
    branding: Branding = Field(default_factory=Branding)
    secretos: dict[str, object] = Field(default_factory=dict)
    config: dict[str, object] = Field(default_factory=dict)
    packs: Packs = Field(default_factory=Packs)
    canal: Canal = Field(default_factory=Canal)
