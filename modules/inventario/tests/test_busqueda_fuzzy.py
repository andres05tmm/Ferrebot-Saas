"""Búsqueda — capas puras (sin BD): normalización, fuzzy conservador y orquestación.

La fuzzy es Python puro (rapidfuzz), así que se prueba sin Postgres. Las capas exacta/trigram/
alias (SQL) se prueban contra base efímera en tests/test_busqueda_db.py.
"""
from modules.inventario.busqueda import (
    BuscadorProductos,
    normalizar,
    sugerencias_fuzzy,
)


def test_normalizar_quita_tildes_y_baja():
    assert normalizar("Martillo  Bellota Ñoño") == "martillo bellota nono"
    assert normalizar("DRYWALL  1/2") == "drywall 1/2"


def test_fuzzy_sugiere_typo_con_palabra_comun():
    sug = sugerencias_fuzzy("tornillo galvanizdo", ["Tornillo Galvanizado", "Martillo"])
    assert sug, "debería sugerir el tornillo con typo"
    assert sug[0][0] == "Tornillo Galvanizado"
    assert sug[0][1] >= 92


def test_fuzzy_descarta_sin_palabra_comun():
    # martillo vs tornillo: sin palabra común > 3 letras → descartado (evita falso positivo).
    assert sugerencias_fuzzy("martillo", ["Tornillo"]) == []


def test_fuzzy_descarta_banda_baja():
    # Comparten "cemento" pero el resto difiere mucho: token_sort_ratio < 92 → descartado.
    assert sugerencias_fuzzy("cemento", ["Cemento Gris Bulto 50kg Premium"]) == []


class _FakeRepo:
    """Repo de búsqueda que solo expone candidatos (exacta/alias/trigram vacíos)."""
    def __init__(self, nombres):
        self._nombres = nombres

    async def buscar_exacta(self, query, limite):
        return []

    async def buscar_alias(self, query):
        return None

    async def buscar_trigram(self, query, umbral, limite):
        return []

    async def nombres_activos(self):
        return self._nombres


async def test_orquestador_cae_a_fuzzy_y_marca_sugerencia():
    repo = _FakeRepo([(1, "Tornillo Galvanizado"), (2, "Martillo")])
    res = await BuscadorProductos(repo).buscar("tornillo galvanizdo")
    assert res.requiere_confirmacion is True
    assert res.coincidencias[0].producto_id == 1
    assert res.coincidencias[0].fuente == "fuzzy"
    assert res.coincidencias[0].sugerencia is True
    # Martillo no comparte palabra significativa con la query → no aparece.
    assert all(c.producto_id != 2 for c in res.coincidencias)
