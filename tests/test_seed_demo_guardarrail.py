"""Guardarraíl anti-pérdida-de-datos de la resiembra (resembrar_slug). PURO: sin red ni BD.

`resembrar_demo` hace DELETE sobre las tablas transaccionales; `resembrar_slug` es el único punto que
convierte un slug en conexión + DELETE, así que ahí vive el guardarraíl. Se afirma: un slug que no es
demo aborta con `SystemExit` ANTES de conectar (las funciones que tocarían la BD se monkeypatchean para
estallar si se llaman); un slug demo válido pasa el guardarraíl y delega normalmente.
"""
from __future__ import annotations

import pytest

import tools.seed_demo_transaccional as seed


class _FakeSettings:
    """Settings mínimo: solo lo que toca `resembrar_slug` (demo_slugs + base de URL)."""

    def __init__(self, slugs: tuple[str, ...]) -> None:
        self._slugs = slugs
        self.tenants_direct_url_base = "postgresql://u@localhost:5433"

    @property
    def demo_slugs(self) -> tuple[str, ...]:
        return self._slugs


def _no_debio_conectar(*_a, **_k):
    raise AssertionError("no debió tocar la BD: el guardarraíl tenía que abortar antes")


@pytest.fixture
def _settings_demo(monkeypatch):
    """Inyecta settings con `barberia-demo` como único demo y bloquea cualquier acceso a la BD."""
    monkeypatch.setattr(seed, "get_settings", lambda: _FakeSettings(("barberia-demo", "ventas-mal")))
    monkeypatch.setattr(seed, "capacidades_efectivas_sync", _no_debio_conectar)
    monkeypatch.setattr(seed, "resembrar_demo", _no_debio_conectar)


def test_slug_no_demo_aborta_sin_conectar(_settings_demo):
    # 'puntorojo' no está en demo_slugs → SystemExit (cinturón 1), sin tocar la BD.
    with pytest.raises(SystemExit, match="no está en DEMO_TENANT_SLUGS"):
        seed.resembrar_slug("puntorojo")


def test_slug_en_lista_pero_sin_sufijo_demo_aborta(_settings_demo):
    # 'ventas-mal' está en la lista (config errónea) pero no termina en '-demo' → SystemExit (cinturón 2).
    with pytest.raises(SystemExit, match="no termina en '-demo'"):
        seed.resembrar_slug("ventas-mal")


def test_slug_demo_valido_pasa_el_guardarrail(monkeypatch):
    # Un slug demo legítimo pasa el guardarraíl y delega en resembrar_demo (aquí stubbeado).
    monkeypatch.setattr(seed, "get_settings", lambda: _FakeSettings(("barberia-demo",)))
    monkeypatch.setattr(seed, "capacidades_efectivas_sync", lambda slug: frozenset({"pack_agenda"}))
    llamado: dict = {}

    def _fake_resembrar(conn_url, capacidades, ahora):
        llamado["conn_url"] = conn_url
        llamado["capacidades"] = capacidades
        return {"citas": 5}

    monkeypatch.setattr(seed, "resembrar_demo", _fake_resembrar)

    assert seed.resembrar_slug("barberia-demo") == {"citas": 5}
    assert llamado["capacidades"] == frozenset({"pack_agenda"})
    assert "ferrebot_barberia-demo" in llamado["conn_url"]
