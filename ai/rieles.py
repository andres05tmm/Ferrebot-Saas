"""Rieles de validación de voz (ADR 0005, decisión c). Funciones PURAS y testeables.

El despachador hace el IO (resuelve catálogo, lee umbrales de `config_empresa`) y le pasa a
cada riel datos ya resueltos; el riel solo decide. Tres rieles, en orden, antes de ejecutar la
herramienta:

  1. `riel_producto`     → producto desconocido/ambiguo: no registra, pregunta (no inventa productos).
  2. `riel_precio`       → precio dudoso: el modelo puso un precio NO declarado por el usuario que
                            difiere del catálogo > tolerancia (def 1 % / mín 1 peso): pregunta.
  3. `riel_confirmacion` → confirmación hablada de gasto/fiado/abono antes de mutar plata.

Cada riel devuelve una `Decision`: `Ejecutar` (sigue), `Preguntar` (corta, recuperable) o
`Confirmar` (corta, pide un sí). El despachador relaya `Preguntar`/`Confirmar` al usuario SIN
ejecutar; la confirmación llega como un turno nuevo reusando la misma `idempotency_key`.
"""
from dataclasses import dataclass
from decimal import Decimal


# --- Resultado de un riel ----------------------------------------------------
@dataclass(frozen=True, slots=True)
class Ejecutar:
    """El riel no bloquea: la herramienta puede ejecutarse."""


@dataclass(frozen=True, slots=True)
class Preguntar:
    """El riel bloquea y pide aclaración. `codigo` espeja un error del envelope (recuperable)."""

    codigo: str          # producto_no_encontrado | producto_ambiguo | precio_dudoso
    mensaje: str


@dataclass(frozen=True, slots=True)
class Confirmar:
    """El riel exige confirmación hablada antes de ejecutar (gasto/fiado/abono)."""

    resumen: str


Decision = Ejecutar | Preguntar | Confirmar


# --- Datos que el despachador resuelve y pasa a los rieles -------------------
@dataclass(frozen=True, slots=True)
class ItemResuelto:
    """Resultado de resolver un ítem contra el catálogo del tenant."""

    referencia: str      # texto para el mensaje al usuario (nombre o "producto {id}")
    candidatos: int      # 0 = no encontrado · 1 = único · >1 = ambiguo


@dataclass(frozen=True, slots=True)
class ItemPrecio:
    """Comparación del total que implica el modelo contra el total que calcula el catálogo."""

    referencia: str
    total_modelo: Decimal
    total_catalogo: Decimal
    declarado: bool      # True si el usuario dijo el precio (precio_dicho_por_usuario): no se cuestiona


# --- Riel 1: producto desconocido / ambiguo ----------------------------------
def riel_producto(items: list[ItemResuelto]) -> Decision:
    """Si algún ítem no resuelve a un único producto, corta y pregunta (no inventa)."""
    for it in items:
        if it.candidatos == 0:
            return Preguntar(
                "producto_no_encontrado",
                f"No encontré ningún producto para «{it.referencia}». ¿Cuál es?",
            )
        if it.candidatos > 1:
            return Preguntar(
                "producto_ambiguo",
                f"Hay varios productos que coinciden con «{it.referencia}». ¿Cuál de ellos?",
            )
    return Ejecutar()


# --- Riel 2: precio dudoso ---------------------------------------------------
def precio_dudoso(
    total_modelo: Decimal,
    total_catalogo: Decimal,
    *,
    tolerancia_pct: Decimal,
    tolerancia_min: Decimal,
) -> bool:
    """True si la diferencia supera la tolerancia: max(pct % del catálogo, mínimo en pesos)."""
    diferencia = abs(total_modelo - total_catalogo)
    tolerancia = max(total_catalogo * tolerancia_pct / Decimal(100), tolerancia_min)
    return diferencia > tolerancia


def riel_precio(
    items: list[ItemPrecio], *, tolerancia_pct: Decimal, tolerancia_min: Decimal
) -> Decision:
    """Solo cuestiona precios que el modelo puso y el usuario NO declaró."""
    for it in items:
        if it.declarado:
            continue
        if precio_dudoso(
            it.total_modelo, it.total_catalogo,
            tolerancia_pct=tolerancia_pct, tolerancia_min=tolerancia_min,
        ):
            return Preguntar(
                "precio_dudoso",
                f"El precio de «{it.referencia}» (${it.total_modelo}) no cuadra con el "
                f"catálogo (${it.total_catalogo}). ¿Lo registro así?",
            )
    return Ejecutar()


# --- Riel 3: confirmación hablada --------------------------------------------
def riel_confirmacion(*, requiere: bool, confirmado: bool, resumen: str) -> Decision:
    """Gasto/fiado/abono: si la empresa exige confirmar y el usuario aún no confirmó, corta."""
    if requiere and not confirmado:
        return Confirmar(resumen)
    return Ejecutar()
