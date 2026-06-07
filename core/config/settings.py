"""Settings de plataforma cargados desde el entorno (.env en local).

Secretos por empresa NO viven aquí: van cifrados en el control DB (ver core.crypto).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Bases de datos (forma base postgresql://... ; los drivers se derivan en core.db.urls)
    admin_database_url: str
    control_database_url: str
    tenants_direct_url_base: str

    # Plataforma
    secret_key: str = "dev-only-change-me"
    secrets_master_key: str = "dev-only-change-me-master"
    base_domain: str = "localhost"
    # Empresa por defecto (opt-in) para despliegues SINGLE-TENANT sin dominio propio (p. ej. el dominio
    # que da Railway, sin subdominio): último recurso de resolución de tenant. None = comportamiento
    # multi-tenant normal (sin fallback). Ver core/tenancy/resolver.py.
    default_tenant_slug: str | None = None
    service_type: str = "api"
    redis_url: str = "redis://localhost:6379/0"
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.0   # solo errores por defecto (barato)

    # Respaldo automático (tools/backup_db.py): apagado por defecto. Se controla con BACKUP_ENABLED
    # en .env.prod (pydantic v2 acepta on/off/true/false/1/0). Ver docs/runbook.md.
    backup_enabled: bool = False

    # Auth
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # IA (plataforma)
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # LLM: proveedor/modelos por defecto de plataforma (override por empresa en config_empresa).
    llm_provider: str = "openai"
    llm_model_worker: str = "gpt-4o-mini"
    llm_model_orquestador: str = "gpt-4o"


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados por proceso. Tests pueden limpiar el caché con get_settings.cache_clear()."""
    return Settings()  # type: ignore[call-arg]
