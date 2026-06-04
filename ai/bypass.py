"""Bypass: camino rápido sin IA para ventas simples (port de `bypass.py`, ferrebot-logica-portar.md §2).

Convergencia (entregable 5.3): cuando el bypass hace match, **no llama a `VentaService`**; emite un
`ToolCall` normalizado y lo entrega a `dispatcher.ejecutar` — el MISMO punto de ejecución del modelo
(rieles + RBAC + idempotencia). No hay rama de lógica duplicada (ai-tools.md §6.3).

La *match-logic* se queda aquí: `analizar` (texto → producto + componentes de cantidad) +
`producto_exacto` (catálogo) + los gates `tiene_escalonado` / `_fraccion_que_coincide`. Si algo no
resuelve, `intentar` devuelve `None` = CaeAlModelo (el turno va al modelo por el loop del agente).
La cantidad se descompone en componentes (entero=precio simple, fracción=precio de fracción) y cada
componente es un ítem del `ToolCall`; así una mixta `1-1/2` da `precio_unidad×1 + fraccion[½]` exacto
sin recalcular precios aquí (el servicio calcula).

Doble lectura (decisión #5, opción b): el bypass ya resolvió el producto con `producto_exacto`, así
que lo deposita en `recursos.resueltos[producto_id]` para que R1 NO lo relea de Postgres en el camino
caliente (~60 % del tráfico).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Protocol

from ai.dispatcher import Dispatcher, Recursos, Respuesta
from ai.envelope import Contexto
from ai.ports import ProductoCatalogo
from core.llm.base import ToolCall
from modules.inventario.precios import EsquemaPrecio, _fraccion_que_coincide

# --- Mapa de fracciones escritas (bypass.py:73-105) --------------------------
_FRAC_ESCRITAS: dict[str, Decimal] = {
    "medio": Decimal("0.5"), "media": Decimal("0.5"),
    "un cuarto": Decimal("0.25"), "cuarto": Decimal("0.25"),
    "tres cuartos": Decimal("0.75"),
    "un octavo": Decimal("0.125"), "octavo": Decimal("0.125"),
}
_PALABRAS_FRAC = "medio|media|un cuarto|cuarto|tres cuartos|un octavo|octavo"

# --- Deshabilitadores (bypass.py:41-66) --------------------------------------
_TOKENS_CLIENTE = {"fiado", "credito", "factura", "abono", "debe", "saldo", "deuda"}
_FRASES_CLIENTE = ("a nombre", "cuenta de")
_TOKENS_CONSULTA = {
    "cuanto", "vale", "precio", "hay", "stock", "queda", "inventario",
    "reporte", "total", "gasto", "ultimo", "ultima",
}
_TOKENS_MODIF = {"cambia", "quita", "agrega", "borra", "corrige", "cancela", "olvida"}
# `para <Nombre propio>` sobre el texto ORIGINAL (la mayúscula distingue persona de sustantivo).
_RE_PARA_NOMBRE = re.compile(r"\bpara\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+")

# --- Patrones de cantidad (orden importa: mixta antes que simple) ------------
_RE_MIXTA_NUM = re.compile(r"^(\d+)\s*[- ]\s*(\d+)/(\d+)\s+(.+)$")
_RE_MIXTA_ESCRITA = re.compile(rf"^(\d+)\s+y\s+({_PALABRAS_FRAC})\s+(.+)$")
_RE_FRAC_NUM = re.compile(r"^(\d+)/(\d+)\s+(.+)$")
_RE_FRAC_ESCRITA = re.compile(rf"^({_PALABRAS_FRAC})\s+(.+)$")
_RE_ENTERO = re.compile(r"^(\d+)\s+(.+)$")


@dataclass(frozen=True, slots=True)
class CaeAlModelo:
    """El bypass no aplica: el turno va al modelo. `motivo` es para logging/depuración."""
    motivo: str


@dataclass(frozen=True, slots=True)
class VentaSimple:
    producto: str                       # slug normalizado para resolver en el catálogo
    componentes: tuple[Decimal, ...]    # cantidades → líneas (entero=simple, <1=fracción)
    cantidad_total: Decimal


Analisis = CaeAlModelo | VentaSimple


# --- Normalización (bypass.py:111) -------------------------------------------
def _sin_tildes(texto: str) -> str:
    desc = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in desc if not unicodedata.combining(c)).replace("ñ", "n")


def _norm_basico(texto: str) -> str:
    """minúsculas + sin tildes/ñ + espacios colapsados; conserva dígitos, `/` y `-`."""
    return " ".join(_sin_tildes(texto).split())


def normalizar_slug(texto: str) -> str:
    """Slug del producto: normaliza, lija `#120 → n120` y limpia especiales (bypass.py:111, `_slug`)."""
    base = re.sub(r"#\s*(\d+)", r"n\1", _norm_basico(texto))
    base = re.sub(r"[^a-z0-9 ]", " ", base)
    return " ".join(base.split())


def _fraccion(numerador: str, denominador: str) -> Decimal | None:
    try:
        return Decimal(numerador) / Decimal(denominador)
    except (InvalidOperation, DivisionByZero):
        return None


def _motivo_deshabilitado(original: str, norm: str) -> str | None:
    if "," in original or "\n" in original:
        return "multiproducto"
    if _RE_PARA_NOMBRE.search(original):
        return "cliente_nombre"
    if any(frase in norm for frase in _FRASES_CLIENTE):
        return "cliente_credito"
    tokens = set(norm.split())
    if tokens & _TOKENS_CLIENTE:
        return "cliente_credito"
    if tokens & _TOKENS_CONSULTA:
        return "consulta"
    if tokens & _TOKENS_MODIF:
        return "modificacion"
    return None


def _parsear_cantidad(norm: str) -> tuple[list[Decimal], str] | None:
    """Devuelve (componentes, texto_producto) según el primer patrón que aplique, o None."""
    if (m := _RE_MIXTA_NUM.match(norm)) is not None:
        frac = _fraccion(m.group(2), m.group(3))
        return ([Decimal(m.group(1)), frac], m.group(4)) if frac is not None else None
    if (m := _RE_MIXTA_ESCRITA.match(norm)) is not None:
        return [Decimal(m.group(1)), _FRAC_ESCRITAS[m.group(2)]], m.group(3)
    if (m := _RE_FRAC_NUM.match(norm)) is not None:
        frac = _fraccion(m.group(1), m.group(2))
        return ([frac], m.group(3)) if frac is not None else None
    if (m := _RE_FRAC_ESCRITA.match(norm)) is not None:
        return [_FRAC_ESCRITAS[m.group(1)]], m.group(2)
    if (m := _RE_ENTERO.match(norm)) is not None:
        return [Decimal(m.group(1))], m.group(2)
    return None


def analizar(texto: str) -> Analisis:
    """Texto libre → intent de venta simple o `CaeAlModelo` (decisión pura, sin BD)."""
    if not texto or not texto.strip():
        return CaeAlModelo("vacio")
    norm = _norm_basico(texto)
    if (motivo := _motivo_deshabilitado(texto, norm)) is not None:
        return CaeAlModelo(motivo)
    parsed = _parsear_cantidad(norm)
    if parsed is None:
        return CaeAlModelo("no_parseable")
    componentes, producto_texto = parsed
    if any(c <= 0 for c in componentes):
        return CaeAlModelo("cantidad_no_positiva")
    producto = normalizar_slug(producto_texto)
    if not producto:
        return CaeAlModelo("sin_producto")
    return VentaSimple(
        producto=producto,
        componentes=tuple(componentes),
        cantidad_total=sum(componentes, Decimal("0")),
    )


# --- Orquestador: intent → ToolCall normalizado → dispatcher.ejecutar --------
@dataclass(frozen=True, slots=True)
class ProductoBypass:
    id: int
    nombre: str
    esquema: EsquemaPrecio


class CatalogoBypass(Protocol):
    """Puerto de catálogo: resuelve un slug a producto SOLO por coincidencia exacta confiable."""

    async def producto_exacto(self, slug: str) -> ProductoBypass | None: ...


class Bypass:
    """Convergencia: el match emite un `ToolCall` a `dispatcher.ejecutar`; nunca llama al servicio.

    `intentar` devuelve la `Respuesta` del despachador (Resultado/ErrorTool/Preguntar/Confirmar) o
    `None` = CaeAlModelo (no-match → el turno va al modelo por el loop del agente).
    """

    def __init__(self, catalogo: CatalogoBypass, dispatcher: Dispatcher) -> None:
        self._catalogo = catalogo
        self._dispatcher = dispatcher

    async def intentar(self, texto: str, ctx: Contexto, recursos: Recursos) -> Respuesta | None:
        """Match → ToolCall normalizado → dispatcher.ejecutar. None = CaeAlModelo (no-match).

        Al hacer match deposita el producto resuelto en `recursos.resueltos` (decisión #5b) para que
        R1 no relea Postgres, y construye `ToolCall(registrar_venta, items=[{producto_id, cantidad}])`
        sin `precio_unitario` (el catálogo es la fuente de verdad → R2 no corre). El `origen` y la
        `idempotency_key` los toma el handler de `ctx` (la API es la misma para bypass y modelo).
        """
        analisis = analizar(texto)
        if isinstance(analisis, CaeAlModelo):
            return None                          # no-match → el turno cae al modelo

        prod = await self._catalogo.producto_exacto(analisis.producto)
        if prod is None:
            return None                          # no exacto → al modelo (sin adivinar)
        if prod.esquema.tiene_escalonado:
            return None                          # mayorista por umbral → al modelo
        for cantidad in analisis.componentes:
            if cantidad % 1 != 0 and _fraccion_que_coincide(prod.esquema, cantidad) is None:
                return None                      # fracción inexistente en el catálogo → al modelo

        # Decisión #5b: el producto ya está resuelto; lo pre-cargo para que R1 no relea Postgres.
        recursos.resueltos[prod.id] = ProductoCatalogo(
            id=prod.id, nombre=prod.nombre, activo=True, esquema=prod.esquema
        )
        tool_call = ToolCall(
            id=f"bypass:{prod.id}",
            name="registrar_venta",
            arguments={
                "items": [
                    {"producto_id": prod.id, "cantidad": cantidad}
                    for cantidad in analisis.componentes
                ],
                "metodo_pago": "efectivo",
            },
        )
        return await self._dispatcher.ejecutar(tool_call, ctx, recursos)
