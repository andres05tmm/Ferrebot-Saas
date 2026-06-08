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
    # Copia OFF-SITE: carpeta local sincronizada a la nube (Google Drive for Desktop). Vacío = sin
    # off-site (solo respaldo local). Si está montada, el backup se copia ahí tras el respaldo local.
    backup_offsite_dir: str = ""

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

    # Canal WhatsApp vía Kapso (BSP). Credenciales de PLATAFORMA: una sola cuenta Kapso atiende a
    # todos los tenants (el tenant se resuelve por phone_number_id en el control DB, tabla wa_numeros).
    # `kapso_webhook_secret` valida la firma HMAC de los webhooks entrantes; `kapso_api_key` autentica
    # el envío saliente. NUNCA hardcodear: van en el entorno.
    kapso_webhook_secret: str = ""
    kapso_api_key: str = ""
    kapso_api_base: str = "https://api.kapso.ai/meta/whatsapp/v24.0"
    # Plantilla (template) aprobada para el recordatorio de reconfirmación de citas (anti-no-show).
    # Es el nombre registrado en la WABA; vacío = el job NO envía recordatorios (queda inactivo).
    kapso_template_recordatorio: str = ""
    kapso_template_recordatorio_idioma: str = "es"

    # Google Calendar (sync OPCIONAL del pack Agenda, write-only). Credencial de PLATAFORMA: el JSON
    # del SERVICE ACCOUNT (no OAuth). El negocio comparte su calendario con el email del SA y guarda
    # solo su `google_calendar_id` por tenant (en agenda_config). Vacío = sync deshabilitado en toda la
    # plataforma. NUNCA hardcodear: va en el entorno (es un secreto). Ver docs/agenda-google-calendar.md.
    google_service_account_json: str = ""


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados por proceso. Tests pueden limpiar el caché con get_settings.cache_clear()."""
    return Settings()  # type: ignore[call-arg]
