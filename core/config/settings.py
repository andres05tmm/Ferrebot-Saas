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
    # CORS quirúrgico (plan Melquiadez §3): origins permitidos a las rutas PÚBLICAS de auth
    # (login/reset) cuando la landing en melquiadez.com hace POST cross-origin a app.melquiadez.com.
    # Coma-separado. Default SEGURO = solo la landing de prod; el resto de la API NUNCA recibe CORS.
    # En DEV se agrega el origin de Vite por env (`CORS_ALLOW_ORIGINS=https://melquiadez.com,http://localhost:5173`)
    # — nunca hardcodeado aquí. Ver apps/api/cors.py.
    cors_allow_origins: str = "https://melquiadez.com"
    # Empresa por defecto (opt-in) para despliegues SINGLE-TENANT sin dominio propio (p. ej. el dominio
    # que da Railway, sin subdominio): último recurso de resolución de tenant. None = comportamiento
    # multi-tenant normal (sin fallback). Ver core/tenancy/resolver.py.
    default_tenant_slug: str | None = None
    service_type: str = "api"
    # Tenants DEMO (superficie pública Melquiadez, plan §4-§5): se marcan por LISTA DE SLUGS en config,
    # NO por una columna `es_demo` en el control DB — es lo más simple y reversible (cero migración) y el
    # único consumidor hoy es el cron de resiembra nocturna. Si el panel super-admin algún día necesita
    # filtrar demos en SQL, se promueve a columna. Coma-separado; vacío = no hay demos que resembrar.
    demo_tenant_slugs: str = "clinica-demo,barberia-demo,restaurante-demo,hotel-demo"
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
    # Login email/contraseña (ADR 0009): lockout por email tras N fallos durante una ventana (Redis).
    login_max_intentos: int = 5
    login_lockout_segundos: int = 300
    # Rate-limit de POST /auth/reset/solicitar (Redis, INCR+EXPIRE). DOS cubos INDEPENDIENTES, 429 si
    # CUALQUIERA pasa su tope; ambos cuentan SIEMPRE (exista o no el email) → no enumera. Ver login lockout.
    #   - Cubo por EMAIL solo (clave sha(email), sin IP): protección real anti email-bombing dirigido,
    #     inmune a la rotación de X-Forwarded-For. Apretado.
    reset_solicitar_max_intentos: int = 3
    reset_solicitar_ventana_segundos: int = 900
    #   - Cubo por IP sola: best-effort (XFF es spoofeable en Railway). Más holgado para no castigar a
    #     usuarios legítimos detrás de una IP/NAT compartida.
    reset_solicitar_ip_max_intentos: int = 30
    reset_solicitar_ip_ventana_segundos: int = 900
    # Tokens de un solo uso para set-password / reset (Redis, hash del token). TTL CORTO (1 h): el enlace
    # de larga vida sería un riesgo. El token YA NO se loguea (solo viaja al usuario); el envío de email
    # real es un TODO aparte (hasta entonces el provisionador entrega el token por su propio canal).
    auth_token_ttl_segundos: int = 3600
    # Estado de los jobs de provisioning del panel (Redis, ADR 0010 §B2): cuánto vive job_id→estado.
    provision_estado_ttl_segundos: int = 86400
    # Reconciliación de facturas (D7.2 del ADR 0012): el cron barre las `pendiente`/`error` con al menos
    # `antiguedad_min` minutos sin desenlace y consulta su estado en MATIAS (red de respaldo del webhook).
    reconciliacion_antiguedad_min_minutos: int = 30
    reconciliacion_lote_max: int = 200   # tope de facturas por tenant y corrida (no saturar MATIAS)

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
    # Plantilla aprobada para el recordatorio de cobranza (ADR 0015). Genérica a propósito (sin el
    # monto: el saldo exacto lo da `mi_saldo` cuando el cliente responde, ya en la ventana de 24h).
    # Vacío = el cron de cobranza NO envía (queda inactivo hasta aprobar la plantilla en la WABA).
    kapso_template_cobranza: str = ""
    kapso_template_cobranza_idioma: str = "es"
    # Plantilla aprobada del seguimiento postventa ("¿cómo te fue? califícanos 1-5"). Vacío = el
    # cron de postventa NO envía (queda inactivo hasta aprobar la plantilla en la WABA).
    kapso_template_postventa: str = ""
    kapso_template_postventa_idioma: str = "es"

    # Google Calendar (sync OPCIONAL del pack Agenda, write-only). Credencial de PLATAFORMA: el JSON
    # del SERVICE ACCOUNT (no OAuth). El negocio comparte su calendario con el email del SA y guarda
    # solo su `google_calendar_id` por tenant (en agenda_config). Vacío = sync deshabilitado en toda la
    # plataforma. NUNCA hardcodear: va en el entorno (es un secreto). Ver docs/agenda-google-calendar.md.
    google_service_account_json: str = ""

    @property
    def demo_slugs(self) -> tuple[str, ...]:
        """Slugs de tenants demo, parseados de `demo_tenant_slugs` (coma-separado, sin vacíos)."""
        return tuple(s.strip() for s in self.demo_tenant_slugs.split(",") if s.strip())

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Origins permitidos para el CORS de auth, parseados de `cors_allow_origins` (coma-separado)."""
        return tuple(o.strip() for o in self.cors_allow_origins.split(",") if o.strip())


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados por proceso. Tests pueden limpiar el caché con get_settings.cache_clear()."""
    return Settings()  # type: ignore[call-arg]
