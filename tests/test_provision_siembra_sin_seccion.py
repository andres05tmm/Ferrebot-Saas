"""FIX (heredado de Fase 0): `siembra_sin_seccion` en el provisionador de packs.

Antes, si un flag de pack estaba activo pero el manifiesto NO declaraba su sección, `_cargar_packs`
lo saltaba SIEMPRE. Para el vertical construcción eso dejaba un tenant SIN sus `parametros_legales`
(cimiento no negociable: constantes de ley que el loader hardcodea, no dato del manifiesto).

El fix: `Pack.siembra_sin_seccion` (default False). Con True (pack `obras`/construcción), el
provisionador corre `pack.loader(None, conn)` aunque falte la sección; con False (pos/agenda/faq/
pedidos) el comportamiento es intacto (se salta). Se prueba con una conexión psycopg FALSA: sin BD.
"""
import psycopg
import pytest

from tools.manifest.packs.registry import PACKS
from tools.manifest.schema import Identidad, Manifiesto
from tools.provision_from_manifest import _cargar_packs


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Captura (sql, params) y hace de context manager, como `with psycopg.connect(...) as conn`."""

    def __init__(self):
        self.ejecutados: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.ejecutados.append((sql, params))
        if " ".join(sql.split()).startswith("SELECT 1 FROM"):
            return _FakeResult(None)   # nada existe aún
        return _FakeResult(None)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def inserts_en(self, tabla: str) -> list[tuple[str, object]]:
        return [(s, p) for s, p in self.ejecutados if f"INSERT INTO {tabla}" in s]


def _manifiesto_sin_packs() -> Manifiesto:
    """Manifiesto mínimo válido SIN ninguna sección `packs.*` (packs.construccion / packs.pedidos = None)."""
    return Manifiesto(
        identidad=Identidad(slug="pim", nombre="Construcciones PIM S.A.S.", nit="901462287")
    )


def _patch_conn(monkeypatch) -> _FakeConn:
    fake = _FakeConn()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: fake)
    return fake


_URL = "postgresql://user:pass@localhost:5432/testdb"


def test_registry_wiring_construccion_true_pedidos_false():
    """Guarda del wiring: solo el pack de construcción activa el flag (los demás lo dejan en False)."""
    assert PACKS["obras"].siembra_sin_seccion is True
    assert PACKS["pack_pedidos"].siembra_sin_seccion is False
    assert PACKS["ventas"].siembra_sin_seccion is False


def test_construccion_siembra_parametros_legales_sin_seccion(monkeypatch):
    """`obras` activo + manifiesto SIN `packs.construccion` → el loader corre igual (seccion=None) y
    siembra `parametros_legales`. Sin sección no hay catálogos → 0 máquinas/herramientas."""
    fake = _patch_conn(monkeypatch)
    _cargar_packs(_manifiesto_sin_packs(), _URL, frozenset({"obras"}))

    assert len(fake.inserts_en("parametros_legales")) == 1   # cimiento sembrado pese a la sección ausente
    assert fake.inserts_en("maquinas") == []                 # sin sección, sin catálogos default
    assert fake.inserts_en("herramientas") == []
    assert fake.commits == 1 and fake.rollbacks == 0


def test_default_false_pack_sin_seccion_se_salta(monkeypatch):
    """Comportamiento intacto para los demás packs: `pack_pedidos` activo pero sin `packs.pedidos`
    (siembra_sin_seccion=False) NO ejecuta su loader — cero INSERTs, solo el commit final."""
    fake = _patch_conn(monkeypatch)
    _cargar_packs(_manifiesto_sin_packs(), _URL, frozenset({"pack_pedidos"}))

    assert fake.ejecutados == []          # el loader ni se invocó (se saltó por sección None)
    assert fake.commits == 1
