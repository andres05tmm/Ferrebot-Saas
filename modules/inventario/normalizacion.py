"""Normalización universal de términos de producto (pre-resolución) — port de
`bot-ventas-ferreteria/alias_manager.py` (G1/G2 de docs/goal-bot-acierto-ventas.md).

Corrige typos y abreviaciones del OFICIO ferretero que aplican a CUALQUIER tenant, ANTES de resolver
el producto en el catálogo (bypass y búsqueda). Es determinista y barato; sube el acierto frente a
entradas "sucias" del mundo real sin apoyarse en el LLM.

Frontera multi-tenant (regla del repo): aquí SOLO van transformaciones UNIVERSALES (typos de
materiales genéricos como thinner/varsol/drywall, notación de lija/puntilla, abreviaturas s.c./c.c.).
Los alias específicos de un producto o marca de un tenant ("cuñete davinci", "vinilo ico blanco",
"cal"→carbonato) viven en la tabla `aliases` por-empresa (datos), NO en este código. Así el
normalizador universal sirve a todos los tenants y el catálogo de cada uno aporta sus propios alias.

Orden de aplicación (espeja `aplicar_aliases_dinamicos`): notación → abreviaturas → typos de palabra.
Opera sobre texto ya en minúsculas y sin tildes (la capa que llama normaliza primero).
"""
from __future__ import annotations

import re

# --- Typos de materiales UNIVERSALES (clave→canónico). Solo materiales genéricos del oficio, NO
# marcas ni nombres de producto de un tenant. Port del subconjunto universal de `_ALIASES_DEFAULT`.
_ALIAS_UNIVERSAL: dict[str, str] = {
    # thinner / varsol (disolventes)
    "tiner": "thinner",
    "tinner": "thinner",
    "barsol": "varsol",
    "barso": "varsol",
    "bar sol": "varsol",
    # wayper (paño/estopa de limpieza) — typos frecuentes
    "waiper": "wayper",
    "weiper": "wayper",
    "waype": "wayper",
    "guayper": "wayper",
    # drywall (lámina/yeso) — gran familia de typos fonéticos
    "drwayll": "drywall",
    "drwayl": "drywall",
    "drwall": "drywall",
    "drawall": "drywall",
    "drywll": "drywall",
    "driwoll": "drywall",
    "drygual": "drywall",
    "drigual": "drywall",
    "draigual": "drywall",
    "draiwol": "drywall",
    "draiwall": "drywall",
    "drywal": "drywall",
    "driwall": "drywall",
    # tornillería / herrajes genéricos
    "tira fondo": "tirafondo",
    "rodachines": "rodachina",
    "rodachin": "rodachina",
    # "3 en 1" (lubricante/aceite genérico)
    "3en1": "3 en 1",
    "3-en-1": "3 en 1",
}

# Orden: las claves multi-palabra primero para que "bar sol" gane sobre "bar"+"sol" sueltos.
_ALIAS_ORDENADO: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(termino)}\b"), canon)
    for termino, canon in sorted(_ALIAS_UNIVERSAL.items(), key=lambda kv: -len(kv[0]))
]

# Wayper (paño de limpieza): se vende por KILO o por UNIDAD, en blanco o de color. Regla del oficio
# (port de `_resolver_wayper`): número pelado SIN palabra de peso = UNIDAD ("2 wayper blanco" = 2 und,
# NO 2 kg). Con kilo/libra/gramo = peso. Sin color = blanco por defecto. Apunta al nombre canónico para
# que el match exacto distinga el producto-unidad del producto-kilo (evita un error de valor grande).
# Los patrones consumen un "unidad" final OPCIONAL para ser idempotentes (no duplicar el sufijo si el
# texto ya viene canonizado, p. ej. "wayper blanco unidad").
_RE_WAYPER = re.compile(r"\bwayper(?:\s+unidad)?\b")
_RE_PESO = re.compile(r"\b(?:kilos?|kg|libras?|gramos?|gr)\b")
_RE_WAYPER_COLOR = re.compile(r"\bwayper\s+de\s+color(?:\s+unidad)?\b")
_RE_WAYPER_BLANCO = re.compile(r"\bwayper\s+(?:blanc[oa]|white|normal)(?:\s+unidad)?\b")


def _resolver_wayper(texto: str) -> str:
    """kilo vs unidad + color → nombre canónico del producto wayper (port de `_resolver_wayper`)."""
    if not _RE_WAYPER.search(texto):
        return texto
    sufijo = "" if _RE_PESO.search(texto) else " unidad"   # sin peso = unidad
    # Más específico primero, con placeholder para no recapturar la forma ya reescrita.
    texto = _RE_WAYPER_COLOR.sub("__WPC__", texto)
    texto = _RE_WAYPER_BLANCO.sub("__WPB__", texto)
    texto = _RE_WAYPER.sub("__WPB__", texto)               # genérico sin color → blanco por defecto
    return texto.replace("__WPC__", f"wayper de color{sufijo}").replace("__WPB__", f"wayper blanco{sufijo}")


def _abreviaturas(texto: str) -> str:
    """Abreviaturas de cabeza de puntilla y notación de medida (universales del oficio).

    `s.c.`/`sc` → "sin cabeza", `c.c.`/`cc` → "con cabeza" (puntillas); `t-N` → `tN` (tipo de vinilo,
    p. ej. t-1 → t1). El `#N` → `nN` de lija lo hace el slug (`normalizar_slug`)."""
    # s.c. / c.c. con puntos, como token aislado (el lookahead evita dejar un punto colgante).
    texto = re.sub(r"\bs\.c\.?(?=\s|$)", "sin cabeza", texto)
    texto = re.sub(r"\bc\.c\.?(?=\s|$)", "con cabeza", texto)
    # sc / cc sueltas SOLO cuando hay "puntilla" en el texto (evita pisar siglas no relacionadas).
    if "puntilla" in texto:
        texto = re.sub(r"\bsc\b", "sin cabeza", texto)
        texto = re.sub(r"\bcc\b", "con cabeza", texto)
    # t-1 → t1 (tipo de vinilo): letra t + guion + dígito.
    texto = re.sub(r"\bt-(\d)\b", r"t\1", texto)
    return texto


def normalizar_terminos(texto: str) -> str:
    """Aplica las transformaciones universales a `texto` (minúsculas, sin tildes) y colapsa espacios.

    Pensado para correr DESPUÉS de la normalización básica (minúsculas/tildes) y ANTES de parsear la
    cantidad o resolver el producto. No toca cantidades ni precios; solo canoniza términos."""
    if not texto:
        return texto
    texto = _abreviaturas(texto)
    for patron, canon in _ALIAS_ORDENADO:
        texto = patron.sub(canon, texto)
    texto = _resolver_wayper(texto)               # después de los alias (waype→wayper ya aplicado)
    return " ".join(texto.split())
