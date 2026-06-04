"""Búsqueda de productos en 3 capas — port de search_service/fuzzy_match/alias_manager (§4).

Orden de resolución:

    1. Exacta/normalizada   → coincidencia directa (case-insensitive) sobre el nombre.
    2. Alias                → mapeo persistente y confirmado (termino → producto/forma canónica).
    3. Trigram (pg_trgm)    → similarity() >= 0.3, tolera typos (drwayll → drywall).
    4. Fuzzy conservador    → rapidfuzz.token_sort_ratio >= 92, exige ≥1 palabra común > 3 letras.
                              SOLO SUGIERE (sugerencia=True): quien decide registrar es el llamador.

Decisión de orden: el alias va antes que trigram/fuzzy porque es un mapeo ya confirmado (resuelve
con confianza, no sugiere); la fuzzy es el último recurso y nunca auto-resuelve (ferrebot-logica-portar.md
§1, rieles de validación). El SQL vive en SqlBusquedaRepository (regla no negociable #2).
"""
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz.fuzz import token_sort_ratio

# Umbrales (constantes del módulo por ahora; migran a config_empresa cuando exista ese módulo).
UMBRAL_TRIGRAM = 0.3
UMBRAL_FUZZY = 92.0
_MIN_LETRAS_PALABRA = 3  # una "palabra común" debe tener más de 3 letras para contar


def normalizar(texto: str) -> str:
    """minúsculas + sin tildes/ñ + espacios colapsados (base para exacta y fuzzy)."""
    desc = unicodedata.normalize("NFKD", texto.lower())
    sin_tildes = "".join(c for c in desc if not unicodedata.combining(c))
    return " ".join(sin_tildes.replace("ñ", "n").split())


def _palabras_significativas(texto: str) -> set[str]:
    return {w for w in normalizar(texto).split() if len(w) > _MIN_LETRAS_PALABRA}


def sugerencias_fuzzy(
    query: str, candidatos: list[str], *, umbral: float = UMBRAL_FUZZY
) -> list[tuple[str, float]]:
    """Fuzzy conservador: ratio >= umbral Y al menos una palabra común > 3 letras.

    La regla de palabra común evita falsos positivos entre productos distintos con sufijo
    parecido (martillo ↛ tornillo). Devuelve (nombre, score) ordenado por score desc.
    """
    qn = normalizar(query)
    palabras_q = _palabras_significativas(query)
    encontrados: list[tuple[str, float]] = []
    for nombre in candidatos:
        score = token_sort_ratio(qn, normalizar(nombre))
        if score < umbral:
            continue
        if not (palabras_q & _palabras_significativas(nombre)):
            continue
        encontrados.append((nombre, score))
    encontrados.sort(key=lambda par: par[1], reverse=True)
    return encontrados


@dataclass(frozen=True, slots=True)
class Coincidencia:
    producto_id: int
    nombre: str
    fuente: str          # exacta | alias | trigram | fuzzy
    score: float
    sugerencia: bool     # True solo para fuzzy: requiere confirmación del llamador


@dataclass(frozen=True, slots=True)
class AliasResuelto:
    termino: str
    reemplazo: str
    producto_id: int | None
    nombre_producto: str | None


@dataclass(frozen=True, slots=True)
class ResultadoBusqueda:
    query: str
    coincidencias: list[Coincidencia] = field(default_factory=list)

    @property
    def requiere_confirmacion(self) -> bool:
        return any(c.sugerencia for c in self.coincidencias)


class BusquedaRepo(Protocol):
    async def buscar_exacta(self, query: str, limite: int) -> list[tuple[int, str]]: ...
    async def buscar_alias(self, query: str) -> AliasResuelto | None: ...
    async def buscar_trigram(
        self, query: str, umbral: float, limite: int
    ) -> list[tuple[int, str, float]]: ...
    async def nombres_activos(self) -> list[tuple[int, str]]: ...


class BuscadorProductos:
    """Orquesta las 4 capas; la fuzzy es el único recurso que sugiere en vez de resolver."""

    def __init__(self, repo: BusquedaRepo) -> None:
        self._repo = repo

    async def buscar(
        self, query: str, *, limite: int = 10, _visto: frozenset[str] = frozenset()
    ) -> ResultadoBusqueda:
        q = query.strip()
        if not q:
            return ResultadoBusqueda(query=query)

        exactas = await self._repo.buscar_exacta(q, limite)
        if exactas:
            return ResultadoBusqueda(q, [_coincidencia(i, n, "exacta", 1.0) for i, n in exactas])

        alias = await self._repo.buscar_alias(q)
        if alias is not None:
            if alias.producto_id is not None:
                return ResultadoBusqueda(
                    q, [_coincidencia(alias.producto_id, alias.nombre_producto or "", "alias", 1.0)]
                )
            # Alias global (sin producto): re-busca la forma canónica, evitando ciclos.
            if alias.reemplazo and normalizar(alias.reemplazo) not in _visto:
                return await self.buscar(
                    alias.reemplazo, limite=limite, _visto=_visto | {normalizar(q)}
                )

        trigram = await self._repo.buscar_trigram(q, UMBRAL_TRIGRAM, limite)
        if trigram:
            return ResultadoBusqueda(
                q, [_coincidencia(i, n, "trigram", float(s)) for i, n, s in trigram]
            )

        candidatos = await self._repo.nombres_activos()
        por_nombre = {n: i for i, n in candidatos}
        sugeridos = sugerencias_fuzzy(q, list(por_nombre))[:limite]
        return ResultadoBusqueda(
            q,
            [
                Coincidencia(por_nombre[n], n, "fuzzy", score / 100, sugerencia=True)
                for n, score in sugeridos
            ],
        )


def _coincidencia(producto_id: int, nombre: str, fuente: str, score: float) -> Coincidencia:
    return Coincidencia(producto_id, nombre, fuente, score, sugerencia=False)
