"""Bypass: camino rápido sin IA para ventas simples (port de `bypass.py`, ferrebot-logica-portar.md §2).

FerreBot resuelve ~60% de los mensajes sin modelo (800ms → <5ms). El bypass NO reimplementa
reglas de negocio: parsea el texto a un *intent* (producto + cantidad) y llama al MISMO
`VentaService` que usaría el tool-calling (ai-tools.md §6.3). La cantidad se descompone en
componentes — entero (precio simple) y fracción (precio de fracción) — y cada componente es una
línea de venta; así una mixta `1-1/2` da `precio_unidad×1 + fraccion[½]` exacto sin recalcular
precios aquí.

Cae al modelo (devuelve None / `CaeAlModelo`) cuando la instrucción es ambigua o sensible:
crédito/cliente, consulta, modificación, multi-producto, producto no exacto, precio escalonado
(mayorista) o fracción inexistente en el catálogo del producto.

Umbral de monto / confirmación hablada: NO se decide aquí; vive en `config_empresa` por empresa
(ADR 0005) y lo aplicará el despachador cuando exista ese módulo.
"""
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Protocol

from modules.inventario.precios import EsquemaPrecio, _fraccion_que_coincide
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import ResultadoVenta, VentaService

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


# --- Orquestador: intent → VentaService (sin duplicar precios) ---------------
@dataclass(frozen=True, slots=True)
class ProductoBypass:
    id: int
    nombre: str
    esquema: EsquemaPrecio


class CatalogoBypass(Protocol):
    """Puerto de catálogo: resuelve un slug a producto SOLO por coincidencia exacta confiable."""

    async def producto_exacto(self, slug: str) -> ProductoBypass | None: ...


class Bypass:
    """Cablea el intent del parser al `VentaService` real. Devuelve None = el turno va al modelo."""

    def __init__(self, catalogo: CatalogoBypass, ventas: VentaService, *, origen: str = "bot") -> None:
        self._catalogo = catalogo
        self._ventas = ventas
        self._origen = origen

    async def intentar(
        self, texto: str, vendedor_id: int, *, idempotency_key: str | None = None
    ) -> ResultadoVenta | None:
        analisis = analizar(texto)
        if isinstance(analisis, CaeAlModelo):
            return None
        prod = await self._catalogo.producto_exacto(analisis.producto)
        if prod is None:
            return None                       # no exacto → al modelo (sin adivinar)
        if prod.esquema.tiene_escalonado:
            return None                       # mayorista por umbral → al modelo
        for cantidad in analisis.componentes:
            if cantidad % 1 != 0 and _fraccion_que_coincide(prod.esquema, cantidad) is None:
                return None                   # fracción inexistente en el catálogo → al modelo
        lineas = [
            VentaDetalleCrear(producto_id=prod.id, cantidad=cantidad)
            for cantidad in analisis.componentes
        ]
        datos = VentaCrear(
            metodo_pago="efectivo", origen=self._origen,
            idempotency_key=idempotency_key, lineas=lineas,
        )
        return await self._ventas.registrar_venta(datos, vendedor_id)
