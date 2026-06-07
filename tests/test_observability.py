"""TDD de `init_sentry`: captura de errores no-op sin DSN, e init real con DSN.

No se toca la red: se monkeypatchea `sentry_sdk.init`/`set_tag` y se inyecta un settings de prueba
(sin DSN → omite; con DSN → inicializa una vez y etiqueta el servicio).
"""
from types import SimpleNamespace

import sentry_sdk

from core import observability
from core.observability import init_sentry


def _settings(dsn: str) -> SimpleNamespace:
    """Stand-in de `Settings` con solo los campos que lee `init_sentry` (evita URLs de BD)."""
    return SimpleNamespace(
        sentry_dsn=dsn,
        sentry_environment="testing",
        sentry_traces_sample_rate=0.0,
    )


def test_init_sentry_sin_dsn_es_noop(monkeypatch):
    llamadas: list = []
    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **k: llamadas.append((a, k)))

    resultado = init_sentry("api", settings=_settings(""))

    assert resultado is False
    assert llamadas == []  # nunca se llamó a sentry_sdk.init


def test_init_sentry_con_dsn_inicializa_una_vez(monkeypatch):
    inits: list[dict] = []
    tags: list[tuple[str, str]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **k: inits.append(k))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda clave, valor: tags.append((clave, valor)))

    resultado = init_sentry("api", settings=_settings("https://dummy@sentry.local/1"))

    assert resultado is True
    assert len(inits) == 1
    assert inits[0]["dsn"] == "https://dummy@sentry.local/1"
    assert inits[0]["send_default_pii"] is False
    assert ("service", "api") in tags
