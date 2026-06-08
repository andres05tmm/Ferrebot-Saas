"""Runtime del worker ARQ (emisión DIAN asíncrona). Smoke manual, no unit-test.

`WorkerSettings` es lo que arranca `arq apps.worker.main.WorkerSettings`. La lógica del job vive en
`apps.worker.jobs` (testeable sin Redis); aquí solo el cableado del runtime: Redis (perezoso, desde
REDIS_URL), tope de reintentos (`MAX_INTENTOS + 1`) y el seam `ctx["crear_servicio"]` que `on_startup`
arma con el wiring real por empresa.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from arq.connections import RedisSettings
from arq.cron import cron

from apps.wa.agent import AgenteWa, MemoriaWa
from apps.wa.kapso import KapsoSender
from apps.worker.jobs import atender_mensaje_wa, emitir_documento
from core.config import get_settings
from core.config.timezone import now_co
from core.db.session import control_session, tenant_session
from core.llm.factory import PlataformaLLM, Turno, get_llm
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from core.logging import get_logger
from core.observability import init_sentry
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import listar_wa_numeros_activos, resolve_tenant_by_id
from modules.agenda.gcal import calendar_client_por_defecto
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.service import AgendaService
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient, MatiasCredenciales
from modules.facturacion.politica import Decision
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import MAX_INTENTOS, FacturacionService

log = get_logger("worker.reconfirmacion")


class _MatiasClientCache:
    """Caché de `MatiasClient` por tenant_id, COMPARTIDA entre jobs del runtime del worker.

    Reusa el token JWT y la caché de ciudades entre emisiones del mismo tenant (antes se construía un
    cliente nuevo por emisión → re-login y recarga de ciudades). El cliente se construye perezoso
    (no toca red). Get-or-create bajo lock: dos jobs del mismo tenant a la vez no crean dos clientes.
    Las credenciales se resuelven por empresa; nunca se mezclan entre tenants (aislamiento).
    """

    def __init__(self, factory: Callable[[MatiasCredenciales], MatiasClient] = MatiasClient) -> None:
        self._factory = factory
        self._clientes: dict[int, MatiasClient] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, tenant_id: int, cred: MatiasCredenciales) -> MatiasClient:
        """Devuelve el cliente cacheado del tenant o lo crea (perezoso) la primera vez."""
        async with self._lock:
            cliente = self._clientes.get(tenant_id)
            if cliente is None:
                cliente = self._factory(cred)
                self._clientes[tenant_id] = cliente
            return cliente


class _ServicioEmision:
    """Adaptador por empresa: resuelve tenant + config (control DB) y emite sobre su base.

    Se crea NUEVO por job, pero reusa la `_MatiasClientCache` del runtime (compartida) para no
    re-autenticar en cada emisión.
    """

    def __init__(self, tenant_id: int, master: str, cache: _MatiasClientCache) -> None:
        self._tid = tenant_id
        self._master = master
        self._cache = cache

    async def emitir(self, factura_id: int) -> Decision:
        async with control_session() as cs:
            tenant = await resolve_tenant_by_id(cs, self._tid)
            cred, config = await cargar_config_matias(cs, self._master, self._tid)
        cliente = await self._cache.get_or_create(self._tid, cred)
        decision: Decision | None = None
        async for s in tenant_session(tenant):   # commit al cerrar el generador (no `return` dentro)
            servicio = FacturacionService(SqlFacturacionRepository(s), cliente, config)
            decision = await servicio.emitir(factura_id)
        return decision


class _ConfigControl:
    """ConfigStore del factory LLM: abre una sesión de control fresca por llamada."""

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        async with control_session() as s:
            return await ControlLLMConfigStore(s).overrides(empresa_id)


class _KeyControl:
    """KeyStore del factory LLM: descifra la key del proveedor en una sesión de control por llamada."""

    def __init__(self, master: str) -> None:
        self._master = master

    async def api_key(self, empresa_id: int, provider: str) -> str | None:
        async with control_session() as s:
            return await ControlLLMKeyStore(s, self._master).api_key(empresa_id, provider)


def _construir_agente(settings) -> AgenteWa:
    """Arma el `AgenteWa` con sus colaboradores reales (control DB, LLM por empresa, Redis, Kapso)."""
    plataforma = PlataformaLLM.desde_settings(settings)
    config_store, key_store = _ConfigControl(), _KeyControl(settings.secrets_master_key)

    async def resolver_llm(tenant_id: int, turno: Turno):
        return await get_llm(
            tenant_id, turno=turno, config_store=config_store, key_store=key_store,
            plataforma=plataforma,
        )

    async def capacidades(tenant_id: int) -> frozenset[str]:
        async with control_session() as s:
            return await ControlCapacidades(s).efectivas(tenant_id)

    return AgenteWa(
        abrir_tenant=tenant_session,
        resolver_llm=resolver_llm,
        capacidades=capacidades,
        memoria=MemoriaWa(url=settings.redis_url),
        sender=KapsoSender(settings.kapso_api_key, base_url=settings.kapso_api_base),
        # Sync write-only con Google Calendar (None si no hay service account en el entorno).
        gcal=calendar_client_por_defecto(),
    )


async def on_startup(ctx: dict) -> None:
    """Inyecta los seams de los jobs: emisión DIAN por empresa y el agente de WhatsApp.

    La `_MatiasClientCache` vive en esta closure (una por runtime), por lo que se comparte entre todos
    los jobs y persiste el cliente —con su token y ciudades— entre emisiones.
    """
    init_sentry("worker")
    settings = get_settings()
    master = settings.secrets_master_key
    cache = _MatiasClientCache()

    async def crear_servicio(tenant_id: int) -> _ServicioEmision:
        return _ServicioEmision(tenant_id, master, cache)

    async def resolver_tenant(tenant_id: int) -> ResolvedTenant | None:
        async with control_session() as s:
            return await resolve_tenant_by_id(s, tenant_id)

    ctx["crear_servicio"] = crear_servicio
    # Canal WhatsApp: resolución de tenant por id + el agente de agenda (bucle LLM + herramientas).
    ctx["resolver_tenant"] = resolver_tenant
    ctx["wa_agente"] = _construir_agente(settings)


def _hacer_enviar_recordatorio(sender: KapsoSender, settings, phone_number_id: str):
    """Closure `enviar(cita) -> bool` que manda la PLANTILLA de reconfirmación por el número del tenant.

    Sin plantilla configurada (`kapso_template_recordatorio` vacío) → no intenta enviar (devuelve False:
    el job no sella el dedup y reintentará cuando se configure). Un fallo de red tampoco rompe el job.
    """
    template = settings.kapso_template_recordatorio

    async def enviar(cita) -> bool:
        if not template:
            return False
        try:
            await sender.enviar_plantilla(
                phone_number_id=phone_number_id, to=cita.cliente_telefono,
                nombre=template, idioma=settings.kapso_template_recordatorio_idioma,
            )
            return True
        except Exception:  # noqa: BLE001 — un fallo de envío no debe tumbar el job
            log.exception("recordatorio_envio_error", cita_id=cita.id)
            return False

    return enviar


async def reconfirmaciones_agenda(ctx: dict) -> str:
    """Cron anti-no-show: por cada tenant con WhatsApp activo y `pack_agenda`, corre la reconfirmación.

    Smoke manual (como el resto de `apps.worker.main`): la lógica determinista vive en
    `AgendaService.procesar_reconfirmaciones` (testeada contra base efímera). Aquí solo el barrido
    multi-tenant: lista los números activos, filtra por capacidad y corre el job sobre la base de cada
    empresa con un `enviar` que usa la plantilla de Kapso del número de ese tenant.
    """
    settings = get_settings()
    sender = KapsoSender(settings.kapso_api_key, base_url=settings.kapso_api_base)
    async with control_session() as cs:
        numeros = await listar_wa_numeros_activos(cs)

    procesadas = 0
    for empresa_id, phone_number_id in numeros:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(empresa_id)
            if "pack_agenda" not in capacidades:
                continue
            tenant = await resolve_tenant_by_id(cs, empresa_id)
        if tenant is None:
            continue
        enviar = _hacer_enviar_recordatorio(sender, settings, phone_number_id)
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = AgendaService(SqlAgendaRepository(s))
            resumen = await servicio.procesar_reconfirmaciones(ahora=now_co(), enviar=enviar)
            procesadas += resumen.recordatorios + resumen.en_riesgo
            log.info(
                "reconfirmaciones_tenant", tenant_id=empresa_id,
                recordatorios=resumen.recordatorios, en_riesgo=resumen.en_riesgo,
            )
    return f"procesadas={procesadas}"


class WorkerSettings:
    """Configuración del worker ARQ (functions, cron, Redis, reintentos)."""

    functions = [emitir_documento, atender_mensaje_wa]
    # Cron anti-no-show: cada 15 min barre todos los tenants (recordatorios + corte de riesgo).
    cron_jobs = [cron(reconfirmaciones_agenda, minute={0, 15, 30, 45}, run_at_startup=False)]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = MAX_INTENTOS + 1
    on_startup = on_startup
