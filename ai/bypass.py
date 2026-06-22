"""Bypass: camino rápido sin IA para ventas simples (port de `bypass.py`, ferrebot-logica-portar.md §2).

Convergencia (entregable 5.3): cuando el bypass hace match, **no llama a `VentaService`**; emite un
`ToolCall` normalizado y lo entrega a `dispatcher.ejecutar` — el MISMO punto de ejecución del modelo
(rieles + RBAC + idempotencia). No hay rama de lógica duplicada (ai-tools.md §6.3).

La *match-logic* se queda aquí: `analizar` (texto → producto + componentes de cantidad) +
`producto_exacto` (catálogo) + el gate `_fraccion_que_coincide` (una fracción sin precio cae al
modelo). El escalonado/mayorista lo resuelve el motor de precios y NO se difiere. Si algo no
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
from modules.inventario.normalizacion import normalizar_terminos
from modules.inventario.precios import (
    EsquemaPrecio,
    _fraccion_que_coincide,
    obtener_precio_para_cantidad,
)

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
# Separa un mensaje multi-producto en ítems ("3 tornillo, 2 chazo" / saltos de línea).
_RE_SEPARADOR_ITEMS = re.compile(r"[,\n]")

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


# Plurales irregulares/frecuentes (bypass.py viejo `_slug`); el resto cae en la regla genérica.
_PLURALES = {"tornillos": "tornillo", "puntillas": "puntilla", "chazos": "chazo", "plasticos": "plastico"}


def _singularizar(texto: str) -> str:
    """Normaliza plurales del slug (port de `_slug`): específicos + regla genérica `(es|s)$` para
    palabras de 4+ letras. Así "galones"→"galon", "tornillos"→"tornillo", sin tocar "t1"/"blanco"."""
    for plural, singular in _PLURALES.items():
        texto = re.sub(rf"\b{plural}\b", singular, texto)
    texto = re.sub(r"\b(\w{4,})es\b", r"\1", texto)
    texto = re.sub(r"\b(\w{4,})s\b", r"\1", texto)
    return texto


# Unidades de empaque/medida que pueden anteceder al producto ("2 galones de thinner" → "thinner").
# Se listan singular y plural + abreviaturas; el slug ya viene singularizado, pero se cubren ambas.
_UNIDADES_INICIALES = frozenset({
    "galon", "galones", "gal",
    "kilo", "kilos", "kg",
    "gramo", "gramos", "gr", "grm",
    "libra", "libras", "lb",
    "metro", "metros", "mts", "mt",
    "centimetro", "centimetros", "cm", "cms",
    "litro", "litros", "lt", "ml", "mlt",
    "bolsa", "bolsas", "tarro", "tarros", "rollo", "rollos",
    "botella", "botellas", "caja", "cajas", "bulto", "bultos",
    "unidad", "unidades", "und",
})


def normalizar_slug(texto: str) -> str:
    """Slug del producto: normaliza, lija `#120 → n120`, limpia especiales y singulariza plurales
    (bypass.py:111, `_slug`)."""
    base = re.sub(r"#\s*(\d+)", r"n\1", _norm_basico(texto))
    base = re.sub(r"[^a-z0-9 ]", " ", base)
    base = _singularizar(" ".join(base.split()))
    return " ".join(base.split())


def quitar_unidad_inicial(texto: str) -> str:
    """Si el slug empieza con una palabra de unidad (galón/kilo/litro/...), seguida opcionalmente de
    "de", la quita: "galon de thinner" → "thinner". Si no empieza con unidad, lo devuelve igual (no
    rompe productos que sí llevan esas palabras en otra posición)."""
    palabras = texto.split()
    if not palabras or palabras[0] not in _UNIDADES_INICIALES:
        return texto
    resto = palabras[1:]
    if resto and resto[0] == "de":
        resto = resto[1:]
    return " ".join(resto)


def unidad_inicial(texto: str) -> str | None:
    """La palabra de unidad con que empieza el slug (o None). Permite saber QUÉ unidad se quitó —p. ej.
    "caja" para los granel: "2 cajas puntilla" son 2 paquetes, no 2 gramos—."""
    palabras = texto.split()
    return palabras[0] if palabras and palabras[0] in _UNIDADES_INICIALES else None


_RE_DE_MEDIDA = re.compile(r"\bde (?=\d)")


def quitar_de_medida(slug: str) -> str:
    """Quita "de" cuando antecede a una MEDIDA numérica del slug: "tornillo drywall de 6x1" →
    "tornillo drywall 6x1" (el catálogo es "TORNILLO DRYWALL 6X1"). Se usa SOLO como reintento de
    resolución, DESPUÉS de probar el slug original: así un producto cuyo nombre SÍ lleva "de N"
    ("tornillo de 5/16") casa directo y no se rompe, y uno donde el vendedor agregó "de" casa al
    reintentar. `producto_exacto` exige match único, así que el reintento nunca adivina."""
    return _RE_DE_MEDIDA.sub("", slug)


def _fraccion(numerador: str, denominador: str) -> Decimal | None:
    try:
        return Decimal(numerador) / Decimal(denominador)
    except (InvalidOperation, DivisionByZero):
        return None


def _motivo_deshabilitado(original: str, norm: str, *, ignorar_para_nombre: bool = False) -> str | None:
    if "," in original or "\n" in original:
        return "multiproducto"
    # "para <Nombre propio>" (mayúscula) suele ser un cliente ("2 tornillo para Juan"), pero también
    # aparece DENTRO de nombres de producto ("Broca para Muro", "Plato para Disco"). El bypass lo
    # reintenta catalog-aware (`ignorar_para_nombre`): si "para X" resuelve a un producto exacto, es
    # producto; si no, defiere como cliente. Ver `_resolver_match`.
    if not ignorar_para_nombre and _RE_PARA_NOMBRE.search(original):
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


def analizar(texto: str, *, ignorar_para_nombre: bool = False) -> Analisis:
    """Texto libre → intent de venta simple o `CaeAlModelo` (decisión pura, sin BD).

    `ignorar_para_nombre` desactiva SOLO el gate "para <Nombre>" (para el reintento catalog-aware del
    bypass: un nombre de producto con "para X" no es un cliente). El resto de gates siguen activos.
    """
    if not texto or not texto.strip():
        return CaeAlModelo("vacio")
    # Normalización universal de términos (typos/abreviaturas del oficio: tiner→thinner, waype→wayper,
    # s.c.→sin cabeza, t-1→t1) ANTES de parsear/resolver. Multi-tenant: solo materiales genéricos; los
    # alias de producto/marca de un tenant van en la tabla `aliases` (datos). No toca cantidad/precio.
    norm = normalizar_terminos(_norm_basico(texto))
    if (motivo := _motivo_deshabilitado(texto, norm, ignorar_para_nombre=ignorar_para_nombre)) is not None:
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


@dataclass(frozen=True, slots=True)
class VentaPreparada:
    """Venta resuelta por el bypass, lista para ofrecer método de pago (todavía NO ejecutada).

    El `tool_call` es `registrar_venta` SIN `metodo_pago` (lo fija el callback del botón); `resumen`
    es la línea + total calculados read-only con el motor de precios (`obtener_precio_para_cantidad`).
    """

    tool_call: ToolCall
    resumen: str


class Bypass:
    """Convergencia: el match emite un `ToolCall` a `dispatcher.ejecutar`; nunca llama al servicio.

    `intentar` devuelve la `Respuesta` del despachador (Resultado/ErrorTool/Preguntar/Confirmar) o
    `None` = CaeAlModelo (no-match → el turno va al modelo por el loop del agente).

    `preparar` es la variante con botones: hace el MISMO match pero NO ejecuta — devuelve la venta
    lista (`VentaPreparada`) para que el handler guarde el pendiente y ofrezca método de pago.
    """

    def __init__(self, catalogo: CatalogoBypass, dispatcher: Dispatcher) -> None:
        self._catalogo = catalogo
        self._dispatcher = dispatcher

    async def _resolver_match(
        self, texto: str, recursos: Recursos
    ) -> tuple[ProductoBypass, tuple[Decimal, ...]] | None:
        """Match común a `intentar`/`preparar`: texto → (producto exacto, componentes) o None.

        Aplica los gates (no exacto / fracción inexistente → None; el escalonado lo resuelve el motor)
        y, al acertar, deposita el producto en `recursos.resueltos` (decisión #5b) para que R1 no relea
        Postgres."""
        analisis = analizar(texto)
        if isinstance(analisis, CaeAlModelo):
            # Excepción catalog-aware al gate "para <Nombre>": un nombre de producto puede llevar
            # "para X" (Broca para Muro, Plato para Disco) y NO ser un cliente. Reparseamos ignorando
            # ese gate y exigimos match EXACTO único: si resuelve, es producto; si no, defiere igual
            # (cliente real como "2 vinilo para Pedro" → sin producto → al modelo). No relaja nada más.
            if analisis.motivo != "cliente_nombre":
                return None                      # otros gates (cliente/consulta/modif/multi) → al modelo
            analisis = analizar(texto, ignorar_para_nombre=True)
            if isinstance(analisis, CaeAlModelo):
                return None

        prod = await self._catalogo.producto_exacto(analisis.producto)
        if prod is None:
            # Reintento: quitar una unidad de empaque inicial ("2 galones de thinner" → "thinner").
            # `producto_exacto` ya exige match único (0/>1 → None), así que solo se usa si es seguro.
            sin_unidad = quitar_unidad_inicial(analisis.producto)
            if sin_unidad and sin_unidad != analisis.producto:
                prod = await self._catalogo.producto_exacto(sin_unidad)
        if prod is None:
            # Reintento: "de" antes de una medida ("tornillo drywall de 6x1" → "...6x1"). Después del
            # slug original, para no romper nombres que SÍ llevan "de N". producto_exacto exige único.
            sin_de = quitar_de_medida(analisis.producto)
            if sin_de != analisis.producto:
                prod = await self._catalogo.producto_exacto(sin_de)
        if prod is None:
            return None                          # no exacto → al modelo (sin adivinar)

        componentes = analisis.componentes
        # Caja de granel por gramo (puntilla): "2 cajas puntilla" son 2 PAQUETES (cajas), no 2 gramos.
        # Para un producto GRM el motor cobra por gramo (÷ tamaño de paquete), así que N cajas = N ×
        # tamaño_paquete gramos → el total queda N × precio_caja (la caja completa, no migajas). Solo
        # GRM: para Cms ("lija esmeril") la "caja" no aplica.
        paquete = prod.esquema.unidades_por_paquete
        if (paquete is not None and prod.esquema.unidad_medida.strip().lower() in {"grm", "gramos"}
                and unidad_inicial(analisis.producto) in {"caja", "cajas"}):
            componentes = tuple(c * paquete for c in componentes)

        # Escalonado (mayorista por umbral): el motor de precios YA resuelve bajo/sobre umbral de forma
        # determinista (precios.py), así que el bypass lo cubre sin caer al modelo. Antes se difería; el
        # cálculo es el mismo que usaría el dispatcher, sin precio del usuario (R2 no corre).
        for cantidad in componentes:
            if cantidad % 1 != 0 and _fraccion_que_coincide(prod.esquema, cantidad) is None:
                return None                      # fracción inexistente en el catálogo → al modelo

        recursos.resueltos[prod.id] = ProductoCatalogo(
            id=prod.id, nombre=prod.nombre, activo=True, esquema=prod.esquema
        )
        return prod, componentes

    def _items(self, componentes: tuple[Decimal, ...], producto_id: int) -> list[dict]:
        """Cada componente → un ítem `{producto_id, cantidad}` (sin `precio_unitario`: manda el catálogo)."""
        return [{"producto_id": producto_id, "cantidad": cantidad} for cantidad in componentes]

    async def _resolver_items(
        self, texto: str, recursos: Recursos
    ) -> list[tuple[ProductoBypass, tuple[Decimal, ...]]] | None:
        """Resuelve uno o VARIOS productos (separados por coma/salto) → lista de (producto, componentes).

        Multi-producto ALL-OR-NOTHING (anti-alucinación): parte el texto y resuelve cada segmento como
        venta simple; si ALGUNO no casa a un producto exacto (o trae cliente/consulta/precio), devuelve
        None y TODO cae al modelo —nunca registra una parte y adivina el resto—. Un solo segmento sigue
        el camino simple intacto. Cada `_resolver_match` deposita su producto en `recursos.resueltos`."""
        segmentos = [s.strip() for s in _RE_SEPARADOR_ITEMS.split(texto) if s.strip()]
        if len(segmentos) <= 1:
            match = await self._resolver_match(texto, recursos)
            return [match] if match is not None else None
        resueltos: list[tuple[ProductoBypass, tuple[Decimal, ...]]] = []
        for seg in segmentos:
            match = await self._resolver_match(seg, recursos)
            if match is None:
                return None                      # un ítem no bypasseable → todo al modelo
            resueltos.append(match)
        return resueltos

    def _items_multi(self, resueltos: list[tuple[ProductoBypass, tuple[Decimal, ...]]]) -> list[dict]:
        items: list[dict] = []
        for prod, componentes in resueltos:
            items.extend(self._items(componentes, prod.id))
        return items

    async def preparar(
        self, texto: str, ctx: Contexto, recursos: Recursos
    ) -> VentaPreparada | None:
        """Match → `VentaPreparada` (ToolCall SIN `metodo_pago` + resumen read-only). None = no-match.

        Hace el MISMO match que `intentar` pero NO ejecuta: arma el `ToolCall` sin `metodo_pago` (lo
        fija el botón) y calcula el resumen con `obtener_precio_para_cantidad` (read-only, sin mutar).
        """
        resueltos = await self._resolver_items(texto, recursos)
        if resueltos is None:
            return None
        tool_call = ToolCall(
            id=f"bypass:{'-'.join(str(p.id) for p, _ in resueltos)}",
            name="registrar_venta",
            arguments={"items": self._items_multi(resueltos)},   # SIN metodo_pago
        )
        return VentaPreparada(tool_call=tool_call, resumen=_resumen_venta_multi(resueltos))

    async def intentar(self, texto: str, ctx: Contexto, recursos: Recursos) -> Respuesta | None:
        """Match → ToolCall normalizado → dispatcher.ejecutar. None = CaeAlModelo (no-match).

        Al hacer match deposita el producto resuelto en `recursos.resueltos` (decisión #5b) para que
        R1 no relea Postgres, y construye `ToolCall(registrar_venta, items=[{producto_id, cantidad}])`
        sin `precio_unitario` (el catálogo es la fuente de verdad → R2 no corre). El `origen` y la
        `idempotency_key` los toma el handler de `ctx` (la API es la misma para bypass y modelo).
        Soporta multi-producto (coma/salto) all-or-nothing vía `_resolver_items`.
        """
        resueltos = await self._resolver_items(texto, recursos)
        if resueltos is None:
            return None
        tool_call = ToolCall(
            id=f"bypass:{'-'.join(str(p.id) for p, _ in resueltos)}",
            name="registrar_venta",
            arguments={"items": self._items_multi(resueltos), "metodo_pago": "efectivo"},
        )
        return await self._dispatcher.ejecutar(tool_call, ctx, recursos)


def _resumen_venta(prod: ProductoBypass, componentes: tuple[Decimal, ...]) -> str:
    """Resumen read-only (líneas + total) de una venta del bypass; texto plano para Telegram.

    Usa `obtener_precio_para_cantidad` (la MISMA verdad de precios del servicio): cada componente es
    una línea y el total es su suma. No registra nada."""
    lineas: list[str] = []
    total = Decimal("0")
    for cantidad in componentes:
        total_linea, _ = obtener_precio_para_cantidad(prod.esquema, cantidad)
        total += total_linea
        lineas.append(f"{_fmt_cantidad(cantidad)} {prod.nombre} = ${_fmt_money(total_linea)}")
    lineas.append(f"Total: ${_fmt_money(total)}")
    lineas.append("¿Con qué se paga?")
    return "\n".join(lineas)


def _resumen_venta_multi(resueltos: list[tuple[ProductoBypass, tuple[Decimal, ...]]]) -> str:
    """Resumen read-only de una venta multi-producto: una línea por componente de cada ítem + total."""
    lineas: list[str] = []
    total = Decimal("0")
    for prod, componentes in resueltos:
        for cantidad in componentes:
            total_linea, _ = obtener_precio_para_cantidad(prod.esquema, cantidad)
            total += total_linea
            lineas.append(f"{_fmt_cantidad(cantidad)} {prod.nombre} = ${_fmt_money(total_linea)}")
    lineas.append(f"Total: ${_fmt_money(total)}")
    lineas.append("¿Con qué se paga?")
    return "\n".join(lineas)


def _fmt_cantidad(cantidad: Decimal) -> str:
    """Cantidad legible: entero sin decimales, fracción como decimal normalizado (0.5, 0.25…)."""
    return str(int(cantidad)) if cantidad % 1 == 0 else str(cantidad.normalize())


def _fmt_money(valor: Decimal) -> str:
    """Pesos con separador de miles '.' (estilo Colombia); sin decimales si es entero."""
    if valor == valor.to_integral_value():
        return f"{int(valor):,}".replace(",", ".")
    return f"{valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
