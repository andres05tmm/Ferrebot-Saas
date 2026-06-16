"""Política de límites por empresa (ai-tools.md §4 y §6.4). Capa SEPARADA del cálculo de negocio.

El RBAC dice QUÉ rol puede ejecutar una herramienta; estos límites dicen CUÁNTO puede sin fricción.
Por empresa (config_empresa, plano de control) se configura: monto máximo de una venta, % máximo de
descuento por línea, y qué hacer al excederlos —pedir confirmación explícita o ESCALAR a un rol
superior (no la ejecuta sola)—. El límite vive en la herramienta, no en el permiso.

Funciones PURAS y testeables (molde de ai.rieles): el despachador resuelve los montos (con el motor de
precios) y la config de la empresa, y le pasa datos ya resueltos; aquí solo se decide. La confirmación
reusa la idempotency_key del turno (un "sí" no duplica), igual que el riel de confirmación (§6.5).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from core.auth.rbac import satisface

# Qué hacer cuando una operación excede un límite.
Modo = Literal["confirmar", "escalar"]

# Claves en config_empresa (texto plano, no secreto).
_CLAVE_MONTO = "venta_monto_max"
_CLAVE_DESCUENTO = "venta_descuento_max_pct"
_CLAVE_MODO = "limite_modo"
_CLAVE_ROL = "limite_rol_minimo"

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class LimitesEmpresa:
    """Política de cuánto puede una operación sin fricción. Defaults = sin tope (no cambia nada)."""

    venta_monto_max: Decimal | None = None      # tope del total de una venta (None = sin tope)
    descuento_max_pct: Decimal | None = None    # % de descuento máximo por línea (None = sin tope)
    modo: Modo = "confirmar"                     # al exceder: pedir confirmación o escalar de rol
    rol_minimo: str = "admin"                    # rol que puede exceder cuando modo = "escalar"

    @property
    def activos(self) -> bool:
        """True si hay algún tope configurado (si no, el despachador ni evalúa)."""
        return self.venta_monto_max is not None or self.descuento_max_pct is not None


# --- Decisiones (el despachador las traduce al envelope) ----------------------
@dataclass(frozen=True, slots=True)
class Permitir:
    """Dentro de los límites (o el rol los supera): ejecutar sin fricción."""


@dataclass(frozen=True, slots=True)
class PedirConfirmacion:
    """Excede un límite y la empresa configuró confirmar: corta y pide un "sí" explícito."""

    resumen: str
    motivos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Escalar:
    """Excede un límite y la empresa configuró escalar: este rol no puede; lo hace uno superior."""

    rol_requerido: str
    detalle: str
    motivos: tuple[str, ...]


Decision = Permitir | PedirConfirmacion | Escalar


def evaluar_venta(
    *, total: Decimal, descuento_pct: Decimal, limites: LimitesEmpresa, rol: str, confirmado: bool
) -> Decision:
    """Decide si una venta se ejecuta, se confirma o se escala según los límites de la empresa (pura).

    `total` y `descuento_pct` ya vienen resueltos (el despachador los calcula con el motor de precios).
    """
    motivos: list[str] = []
    if limites.venta_monto_max is not None and total > limites.venta_monto_max:
        motivos.append(f"el monto ${total} supera el máximo ${limites.venta_monto_max}")
    if limites.descuento_max_pct is not None and descuento_pct > limites.descuento_max_pct:
        motivos.append(f"el descuento {descuento_pct}% supera el máximo {limites.descuento_max_pct}%")

    if not motivos:
        return Permitir()

    if limites.modo == "escalar":
        # El rol superior SÍ puede exceder; el insuficiente no la ejecuta solo.
        if satisface(rol, limites.rol_minimo):
            return Permitir()
        return Escalar(
            rol_requerido=limites.rol_minimo,
            detalle=f"Requiere un {limites.rol_minimo}: " + "; ".join(motivos),
            motivos=tuple(motivos),
        )

    # modo == "confirmar": un "sí" (mismo turno, misma idempotency_key) deja pasar.
    if confirmado:
        return Permitir()
    return PedirConfirmacion(
        resumen="La operación excede los límites (" + "; ".join(motivos) + "). ¿Confirmo?",
        motivos=tuple(motivos),
    )


def limites_desde_overrides(overrides: dict[str, str]) -> LimitesEmpresa:
    """Arma `LimitesEmpresa` desde config_empresa (clave→valor texto), con defaults sin tope."""
    modo: Modo = "escalar" if overrides.get(_CLAVE_MODO, "").strip().lower() == "escalar" else "confirmar"
    return LimitesEmpresa(
        venta_monto_max=_decimal_pos(overrides.get(_CLAVE_MONTO)),
        descuento_max_pct=_decimal_pos(overrides.get(_CLAVE_DESCUENTO)),
        modo=modo,
        rol_minimo=(overrides.get(_CLAVE_ROL) or "admin").strip(),
    )


def _decimal_pos(valor: str | None) -> Decimal | None:
    """Decimal > 0 desde texto, o None (ausente/invalid/≤0 → sin tope; default seguro)."""
    if valor is None or not valor.strip():
        return None
    try:
        d = Decimal(valor)
    except (InvalidOperation, ValueError):
        return None
    return d.quantize(_CENT) if d > 0 else None
