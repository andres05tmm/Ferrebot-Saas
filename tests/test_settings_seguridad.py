"""Fail-fast de claves criptográficas (core/config/settings.py).

Un deploy de producción (ENTORNO=production) JAMÁS debe arrancar con `secret_key` o
`secrets_master_key` en su default de dev: firmaría JWT y cifraría los secretos por empresa con
claves públicas del repo. En dev (default) los defaults siguen sirviendo para levantar sin .env.
"""
import pytest
from pydantic import ValidationError

from core.config.settings import Settings

_BASE = {
    "admin_database_url": "postgresql://u:p@x/admin",
    "control_database_url": "postgresql://u:p@x/control",
    "tenants_direct_url_base": "postgresql://u:p@x",
}


def test_dev_arranca_con_defaults():
    s = Settings(**_BASE, _env_file=None)
    assert s.entorno == "dev" and s.secret_key == "dev-only-change-me"


def test_produccion_con_claves_default_aborta():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**_BASE, entorno="production", _env_file=None)


def test_produccion_con_una_sola_clave_rotada_tambien_aborta():
    with pytest.raises(ValidationError):
        Settings(**_BASE, entorno="production", secret_key="clave-real-fuerte", _env_file=None)


def test_produccion_con_ambas_claves_reales_arranca():
    s = Settings(
        **_BASE, entorno="production",
        secret_key="clave-real-fuerte", secrets_master_key="master-real-fuerte",
        _env_file=None,
    )
    assert s.entorno == "production"
