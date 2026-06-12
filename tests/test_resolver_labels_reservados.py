"""Labels reservados en `_slug_from_host` (plan Melquiadez §3, pre-wildcard DNS).

Con `*.melquiadez.com` apuntando a la API, `app.melquiadez.com` (la entrada de clientes) llegaría con
un subdominio real: sin la lista de reservados el resolver lo tomaría como tenant "app" y NUNCA
caería al claim del JWT. Estas pruebas fijan que los labels reservados (`app`, `api`, `www`, `admin`)
se tratan como "sin subdominio" (sigue la cadena normal de señales) y que un subdominio de verdad
sigue resolviendo como siempre.
"""
import pytest
from starlette.requests import HTTPConnection

from core.auth.jwt import create_access_token
from core.tenancy.resolver import LABELS_RESERVADOS, resolve_slug


def _conn(*, host: str, headers: dict[str, str] | None = None) -> HTTPConnection:
    raw = [(b"host", host.encode())]
    for clave, valor in (headers or {}).items():
        raw.append((clave.lower().encode(), valor.encode()))
    return HTTPConnection({"type": "http", "headers": raw, "state": {}})


@pytest.fixture
def settings_env(monkeypatch):
    """Setea env de plataforma y limpia el caché de settings (antes y después del test)."""
    from core.config import get_settings

    def _set(**env: str | None) -> None:
        for clave, valor in env.items():
            if valor is None:
                monkeypatch.delenv(clave, raising=False)
            else:
                monkeypatch.setenv(clave, valor)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def test_app_con_jwt_resuelve_por_el_claim(settings_env):
    """app.melquiadez.com + JWT con claim `tenant` → resuelve por el claim, no por "app"."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    token = create_access_token(user_id=1, tenant="puntorojo", rol="admin")
    conn = _conn(host="app.melquiadez.com", headers={"authorization": f"Bearer {token}"})
    assert resolve_slug(conn) == "puntorojo"


@pytest.mark.parametrize("label", sorted(LABELS_RESERVADOS))
def test_label_reservado_sin_otras_senales_es_none(settings_env, label):
    """Un label reservado solo, sin header/JWT/default → None (como si no hubiera subdominio)."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    assert resolve_slug(_conn(host=f"{label}.melquiadez.com")) is None


def test_label_reservado_cae_al_header(settings_env):
    """app.melquiadez.com + X-Tenant-Slug → la cadena sigue con el header."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    conn = _conn(host="app.melquiadez.com", headers={"x-tenant-slug": "otra"})
    assert resolve_slug(conn) == "otra"


def test_label_reservado_ignora_mayusculas_y_puerto(settings_env):
    """API.melquiadez.com:8443 también es reservado (el host se normaliza antes de comparar)."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    assert resolve_slug(_conn(host="API.melquiadez.com:8443")) is None


def test_subdominio_de_tenant_resuelve_como_siempre(settings_env):
    """barberia-demo.melquiadez.com → "barberia-demo" (un slug real no se ve afectado)."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    assert resolve_slug(_conn(host="barberia-demo.melquiadez.com")) == "barberia-demo"


def test_subdominio_que_contiene_un_reservado_no_es_reservado(settings_env):
    """Solo el label EXACTO es reservado: "app-demo" o "api2" siguen siendo slugs válidos."""
    settings_env(DEFAULT_TENANT_SLUG=None, BASE_DOMAIN="melquiadez.com")
    assert resolve_slug(_conn(host="app-demo.melquiadez.com")) == "app-demo"
    assert resolve_slug(_conn(host="api2.melquiadez.com")) == "api2"
