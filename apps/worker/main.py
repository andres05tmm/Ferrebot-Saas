"""Runtime del worker ARQ (emisión DIAN asíncrona). Smoke manual, no unit-test.

`WorkerSettings` es lo que arranca `arq apps.worker.main.WorkerSettings`. La lógica del job vive en
`apps.worker.jobs` (testeable sin Redis); aquí solo el cableado del runtime: Redis (perezoso, desde
REDIS_URL), tope de reintentos (`MAX_INTENTOS + 1`) y el seam `ctx["crear_servicio"]` que `on_startup`
arma con el wiring real por empresa.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

from arq.connections import RedisSettings
from arq.cron import cron

from apps.wa.agent import AgenteWa, MemoriaWa
from apps.wa.kapso import KapsoSender
from apps.worker.jobs import (
    _encolar_descarga,
    atender_mensaje_wa,
    descargar_documento,
    emitir_documento,
    procesar_webhook_matias,
    provisionar_tenant,
)
from core.config import get_settings
from core.config.timezone import now_co
from core.db.session import control_session, tenant_session
from core.llm.factory import PlataformaLLM, Turno, get_llm
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from core.logging import get_logger
from core.observability import init_sentry
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import listar_tenants, listar_wa_numeros_activos, resolve_tenant_by_id
from modules.agenda.gcal import calendar_client_por_defecto
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.service import AgendaService
from core.pagos.bold import BoldClient
from core.pagos.config import cargar_config_bold
from modules.cobranza.repository import SqlCobranzaRepository
from modules.cobranza.service import CobranzaService, DeudorRecordatorio
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService
from modules.postventa.repository import SqlPostventaRepository
from modules.postventa.service import PostventaService, SeguimientoPendiente
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient, MatiasCredenciales
from modules.facturacion.politica import Decision
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import MAX_INTENTOS, FacturacionService, ResumenReconciliacion

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

    async def _componer(self) -> tuple:
        """Resuelve tenant + descifra config + cliente MATIAS cacheado (wiring común emitir/descargar)."""
        async with control_session() as cs:
            tenant = await resolve_tenant_by_id(cs, self._tid)
            cred, config = await cargar_config_matias(cs, self._master, self._tid)
        cliente = await self._cache.get_or_create(self._tid, cred)
        return tenant, cliente, config

    async def emitir(self, factura_id: int) -> Decision:
        tenant, cliente, config = await self._componer()
        decision: Decision | None = None
        async for s in tenant_session(tenant):   # commit al cerrar el generador (no `return` dentro)
            servicio = FacturacionService(SqlFacturacionRepository(s), cliente, config)
            decision = await servicio.emitir(factura_id)
        return decision

    async def descargar_documento(self, factura_id: int) -> bool:
        """Archiva el XML de la factura aceptada sobre la base del tenant (D7.3)."""
        tenant, cliente, config = await self._componer()
        ok: bool = True
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = FacturacionService(SqlFacturacionRepository(s), cliente, config)
            ok = await servicio.descargar_documento(factura_id)
        return ok

    async def procesar_webhook(self, recibido_id: int) -> tuple[str, int | None]:
        """Lee el webhook registrado, lo aplica a la factura y lo sella como procesado (D7.1)."""
        tenant, cliente, config = await self._componer()
        resultado: tuple[str, int | None] = ("sin_recibido", None)
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            repo = SqlFacturacionRepository(s)
            recibido = await repo.leer_recibido(recibido_id)
            if recibido is None:
                resultado = ("sin_recibido", None)
                continue
            servicio = FacturacionService(repo, cliente, config)
            resultado = await servicio.aplicar_evento_dian(recibido.evento, recibido.payload)
            await repo.marcar_recibido_procesado(recibido_id)
        return resultado

    async def reconciliar(self, *, antiguedad, limite: int) -> ResumenReconciliacion:
        """Barre las facturas estancadas del tenant y consulta su estado en MATIAS (D7.2)."""
        tenant, cliente, config = await self._componer()
        resumen = ResumenReconciliacion()
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = FacturacionService(SqlFacturacionRepository(s), cliente, config)
            resumen = await servicio.reconciliar(antiguedad=antiguedad, limite=limite)
        return resumen


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

    async def resolver_psp(tenant_id: int):
        """PSP Bold del tenant (ADR 0013): None si no tiene llave (modo manual)."""
        async with control_session() as s:
            cred = await cargar_config_bold(s, settings.secrets_master_key, tenant_id)
        return BoldClient(cred) if cred is not None else None

    return AgenteWa(
        abrir_tenant=tenant_session,
        resolver_llm=resolver_llm,
        capacidades=capacidades,
        memoria=MemoriaWa(url=settings.redis_url),
        sender=KapsoSender(settings.kapso_api_key, base_url=settings.kapso_api_base),
        # Sync write-only con Google Calendar (None si no hay service account en el entorno).
        gcal=calendar_client_por_defecto(),
        resolver_psp=resolver_psp,
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


def _hacer_enviar_cobranza(sender: KapsoSender, settings, phone_number_id: str):
    """Closure `enviar(deudor) -> bool` que manda la PLANTILLA de cobranza por el número del tenant.

    Sin plantilla configurada (`kapso_template_cobranza` vacío) → no intenta enviar (devuelve False:
    el motor no sella el dedup y reintentará cuando se configure). Un fallo de red tampoco rompe el job.
    """
    template = settings.kapso_template_cobranza

    async def enviar(deudor: DeudorRecordatorio) -> bool:
        if not template:
            return False
        try:
            await sender.enviar_plantilla(
                phone_number_id=phone_number_id, to=deudor.telefono,
                nombre=template, idioma=settings.kapso_template_cobranza_idioma,
            )
            return True
        except Exception:  # noqa: BLE001 — un fallo de envío no debe tumbar el job
            log.exception("cobranza_envio_error", cliente_id=deudor.cliente_id)
            return False

    return enviar


async def recordatorios_cobranza(ctx: dict) -> str:
    """Cron de cartera (ADR 0015): por cada tenant con WhatsApp activo y `pack_cobranza`, corre el motor.

    Smoke manual (como `reconfirmaciones_agenda`): la lógica determinista (cadencia, tope, ventana
    horaria, opt-out, promesas) vive en `CobranzaService.procesar_recordatorios` (testeada contra base
    efímera). Aquí solo el barrido multi-tenant con el `enviar` de la plantilla paga de Kapso.
    """
    settings = get_settings()
    sender = KapsoSender(settings.kapso_api_key, base_url=settings.kapso_api_base)
    async with control_session() as cs:
        numeros = await listar_wa_numeros_activos(cs)

    enviados = 0
    for empresa_id, phone_number_id in numeros:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(empresa_id)
            if "pack_cobranza" not in capacidades:
                continue
            tenant = await resolve_tenant_by_id(cs, empresa_id)
        if tenant is None:
            continue
        enviar = _hacer_enviar_cobranza(sender, settings, phone_number_id)
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = CobranzaService(SqlCobranzaRepository(s))
            resumen = await servicio.procesar_recordatorios(ahora=now_co(), enviar=enviar)
            enviados += resumen.recordatorios
            log.info(
                "cobranza_tenant", tenant_id=empresa_id, recordatorios=resumen.recordatorios,
                promesas_incumplidas=resumen.promesas_incumplidas, al_dia=resumen.al_dia,
            )
    return f"enviados={enviados}"


def _hacer_enviar_postventa(sender: KapsoSender, settings, phone_number_id: str):
    """Closure `enviar(seguimiento) -> bool` que manda la PLANTILLA de postventa por el número del tenant.

    Sin plantilla configurada (`kapso_template_postventa` vacío) → False (el motor no sella el dedup
    y reintenta cuando se configure). Un fallo de red tampoco rompe el job.
    """
    template = settings.kapso_template_postventa

    async def enviar(seguimiento: SeguimientoPendiente) -> bool:
        if not template:
            return False
        try:
            await sender.enviar_plantilla(
                phone_number_id=phone_number_id, to=seguimiento.telefono,
                nombre=template, idioma=settings.kapso_template_postventa_idioma,
            )
            return True
        except Exception:  # noqa: BLE001 — un fallo de envío no debe tumbar el job
            log.exception("postventa_envio_error", origen=seguimiento.origen,
                          origen_id=seguimiento.origen_id)
            return False

    return enviar


async def seguimientos_postventa(ctx: dict) -> str:
    """Cron de postventa (plan §2.6): por cada tenant con WhatsApp activo y `pack_postventa`, corre
    el barrido de citas cumplidas / pedidos entregados (el motor aplica dedup y la espera tras el evento).
    """
    settings = get_settings()
    sender = KapsoSender(settings.kapso_api_key, base_url=settings.kapso_api_base)
    async with control_session() as cs:
        numeros = await listar_wa_numeros_activos(cs)

    enviados = 0
    for empresa_id, phone_number_id in numeros:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(empresa_id)
            if "pack_postventa" not in capacidades:
                continue
            tenant = await resolve_tenant_by_id(cs, empresa_id)
        if tenant is None:
            continue
        enviar = _hacer_enviar_postventa(sender, settings, phone_number_id)
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = PostventaService(SqlPostventaRepository(s))
            resumen = await servicio.procesar_seguimientos(ahora=now_co(), enviar=enviar)
            enviados += resumen.enviados
            if resumen.enviados:
                log.info("postventa_tenant", tenant_id=empresa_id, enviados=resumen.enviados)
    return f"enviados={enviados}"


async def conciliar_cobros(ctx: dict) -> str:
    """Cron del frente de pagos (ADR 0013): por cada tenant con `pagos_online` y llave Bold, consulta
    el estado de sus cobros pendientes (polling; el webhook de Bold llega en v1.1 con su spec real).

    Smoke manual (como los demás crons): la lógica vive en `PagosService.conciliar` (testeada con un
    PSP falso). Aquí el barrido multi-tenant: capacidad + credencial Bold descifrada por empresa.
    """
    settings = get_settings()
    async with control_session() as cs:
        tenants = await listar_tenants(cs)

    pagados = 0
    for t in tenants:
        if "pagos_online" not in t.features:
            continue
        async with control_session() as cs:
            cred = await cargar_config_bold(cs, settings.secrets_master_key, t.id)
            tenant = await resolve_tenant_by_id(cs, t.id)
        if cred is None or tenant is None:
            continue                      # sin llave Bold → modo manual, nada que conciliar
        psp = BoldClient(cred)
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            servicio = PagosService(SqlPagosRepository(s), psp=psp)
            resumen = await servicio.conciliar()
            pagados += resumen.pagados
            if resumen.revisados:
                log.info(
                    "conciliar_cobros_tenant", tenant_id=t.id, revisados=resumen.revisados,
                    pagados=resumen.pagados, cerrados=resumen.cerrados,
                )
    return f"pagados={pagados}"


async def reconciliar_pendientes(ctx: dict) -> str:
    """Cron de reconciliación (D7.2): por cada tenant con `facturacion_electronica`, consulta en MATIAS el
    estado de las facturas estancadas y cierra el dead-letter silencioso (red de respaldo del webhook).

    Smoke manual (como `reconfirmaciones_agenda`): la lógica determinista vive en
    `FacturacionService.reconciliar` (testeada con fakes). Aquí solo el barrido multi-tenant: filtra por
    capacidad, reusa el seam `crear_servicio` (con su caché de clientes MATIAS) y encola el archivado del
    XML de las que pasaron a aceptada. `antiguedad`/`lote` son configurables (settings)."""
    settings = get_settings()
    corte = now_co() - timedelta(minutes=settings.reconciliacion_antiguedad_min_minutos)
    async with control_session() as cs:
        tenants = await listar_tenants(cs)

    reconciliadas = 0
    for t in tenants:
        if "facturacion_electronica" not in t.features:
            continue
        servicio = await ctx["crear_servicio"](t.id)
        resumen = await servicio.reconciliar(antiguedad=corte, limite=settings.reconciliacion_lote_max)
        for factura_id in resumen.ids_aceptadas:
            await _encolar_descarga(ctx, t.id, factura_id)
        reconciliadas += resumen.aceptadas + resumen.rechazadas
        if resumen.revisadas:
            log.info(
                "reconciliar_tenant", tenant_id=t.id, revisadas=resumen.revisadas,
                aceptadas=resumen.aceptadas, rechazadas=resumen.rechazadas,
            )
    return f"reconciliadas={reconciliadas}"


class WorkerSettings:
    """Configuración del worker ARQ (functions, cron, Redis, reintentos)."""

    functions = [
        emitir_documento, descargar_documento, procesar_webhook_matias,
        atender_mensaje_wa, provisionar_tenant,
    ]
    cron_jobs = [
        # Cron anti-no-show: cada 15 min barre todos los tenants (recordatorios + corte de riesgo).
        cron(reconfirmaciones_agenda, minute={0, 15, 30, 45}, run_at_startup=False),
        # Reconciliación fiscal (D7.2): cada 10 min consulta el estado de las facturas estancadas.
        cron(reconciliar_pendientes, minute=set(range(0, 60, 10)), run_at_startup=False),
        # Cobranza (ADR 0015): cada 30 min; la ventana horaria/cadencia/tope los aplica el motor.
        cron(recordatorios_cobranza, minute={5, 35}, run_at_startup=False),
        # Pagos (ADR 0013): conciliación por polling cada 5 min (links pagados → estado + SSE).
        cron(conciliar_cobros, minute=set(range(2, 60, 5)), run_at_startup=False),
        # Postventa (plan §2.6): cada hora; la espera tras el evento y el dedup los aplica el motor.
        cron(seguimientos_postventa, minute={50}, run_at_startup=False),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = MAX_INTENTOS + 1
    on_startup = on_startup
