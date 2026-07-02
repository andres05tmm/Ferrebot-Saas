"""Composition root del servicio bot: ensambla `BotDeps` desde los puertos reales.

Decisión arquitectónica (no re-litigar): la sesión de control es **per-call**. Cada wrapper
(`ResolverControl`, `SecretosControl`, `CapacidadesControl`, `ConfigControl`, `KeyControl`) abre una
`AsyncSession` de control FRESCA por llamada (vía `abrir_control`) y delega en las clases existentes
(`resolve_tenant_by_slug`, `ControlSecretosBot`, `ControlCapacidades`, `ControlLLM*Store`). Nada de
una sola sesión long-lived compartida entre requests.

`construir_deps` expone *seams* inyectables con defaults reales: el smoke test pasa fakes y verifica
el cableado sin red; producción usa los defaults (control DB, Redis, RecursosBot, dispatcher). La
construcción NO abre sesiones ni hace I/O: todo queda diferido a las llamadas.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from ai.bypass import Bypass
from ai.confirmacion import ConfirmStore, VentaPendienteStore
from ai.dispatcher import Dispatcher, Recursos
from ai.ports import CatalogoDesdeVentas, ControlUmbralesStore
from ai.tools import Deps
from ai.turno import crear_callback_handler, crear_turno_handler
from apps.bot.catalogo import CatalogoBypassExacto
from apps.bot.ports import BotDeps, DedupStore, RecursosBot, SesionTenant
from apps.bot.recursos import Credenciales
from apps.bot.recursos import RecursosBot as RecursosBotImpl
from apps.bot.redis_stores import RedisConfirmStore, RedisDedupStore, RedisVentaPendienteStore
from apps.bot.repos import ControlCapacidades, ControlSecretosBot, SqlUsuariosBotRepo
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.llm.factory import PlataformaLLM, Turno
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from core.tenancy.cache import control_cache
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_slug
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.clientes.repository import SqlClientesRepository
from modules.clientes.service import ClientesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.inventario.repository import SqlInventarioRepository
from modules.memoria.repository import SqlCostosRepository, SqlMemoriaRepository
from modules.memoria.service import MemoriaService
from modules.facturacion.pos_hook import CierrePos
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService

# Abre una sesión del control DB (CM) por llamada. En prod = core.db.session.control_session.
ControlSession = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ResolverControl:
    """Satisface `ports.ResolverTenant`. Resuelve la empresa por slug abriendo una sesión de control
    FRESCA por llamada y delegando en `resolve_tenant_by_slug`; reusa `control_cache` (TTL corto)
    para no pegarle al control DB en cada update."""

    def __init__(self, abrir_control: ControlSession) -> None:
        self._abrir = abrir_control

    async def por_slug(self, slug: str) -> ResolvedTenant | None:
        cacheado = control_cache.get(slug)
        if cacheado is not None:
            return cacheado
        async with self._abrir() as s:
            tenant = await resolve_tenant_by_slug(s, slug)
        if tenant is not None:
            control_cache.set(tenant)
        return tenant


class SecretosControl:
    """Satisface `ports.SecretosBot`. Por llamada: `ControlSecretosBot(s, master)` sobre una sesión
    de control fresca."""

    def __init__(self, abrir_control: ControlSession, master: str) -> None:
        self._abrir = abrir_control
        self._master = master

    async def webhook_secret(self, empresa_id: int) -> str | None:
        async with self._abrir() as s:
            return await ControlSecretosBot(s, self._master).webhook_secret(empresa_id)

    async def bot_token(self, empresa_id: int) -> str | None:
        async with self._abrir() as s:
            return await ControlSecretosBot(s, self._master).bot_token(empresa_id)


class CapacidadesControl:
    """Satisface `ports.CapacidadesStore`. Por llamada: `ControlCapacidades(s).efectivas(eid)`."""

    def __init__(self, abrir_control: ControlSession) -> None:
        self._abrir = abrir_control

    async def efectivas(self, empresa_id: int) -> frozenset[str]:
        async with self._abrir() as s:
            return await ControlCapacidades(s).efectivas(empresa_id)


class RubroControl:
    """Satisface `ports.RubroStore`. Por llamada: `cargar_rubro(s, eid)` sobre una sesión de control
    fresca (mismo patrón per-call que CapacidadesControl; un update de bot tolera la lectura extra)."""

    def __init__(self, abrir_control: ControlSession) -> None:
        self._abrir = abrir_control

    async def rubro(self, empresa_id: int) -> str | None:
        async with self._abrir() as s:
            return await cargar_rubro(s, empresa_id)


class ConfigControl:
    """Satisface `core.llm.factory.ConfigStore`. Por llamada: `ControlLLMConfigStore(s).overrides(eid)`."""

    def __init__(self, abrir_control: ControlSession) -> None:
        self._abrir = abrir_control

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        async with self._abrir() as s:
            return await ControlLLMConfigStore(s).overrides(empresa_id)


class KeyControl:
    """Satisface `core.llm.factory.KeyStore`. Por llamada: `ControlLLMKeyStore(s, master).api_key(...)`."""

    def __init__(self, abrir_control: ControlSession, master: str) -> None:
        self._abrir = abrir_control
        self._master = master

    async def api_key(self, empresa_id: int, provider: str) -> str | None:
        async with self._abrir() as s:
            return await ControlLLMKeyStore(s, self._master).api_key(empresa_id, provider)


def _crear_cargar(
    abrir_control: ControlSession, master: str
) -> Callable[[int], Awaitable[Credenciales]]:
    """Loader de `RecursosBot`: una sola sesión de control por empresa → (bot_token, openai_key)."""

    async def _cargar(empresa_id: int) -> Credenciales:
        async with abrir_control() as s:
            bot_token = await ControlSecretosBot(s, master).bot_token(empresa_id)
            openai = await ControlLLMKeyStore(s, master).api_key(empresa_id, "openai")
        return Credenciales(bot_token=bot_token, openai_key=openai)

    return _cargar


def _crear_recursos_factory(config: ConfigControl) -> Callable[[AsyncSession], Recursos]:
    """Devuelve `crear_recursos(session)` → `Recursos` FRESCO por turno (servicios atados a esa sesión).
    Los umbrales salen del control (config_empresa), NO de la sesión del tenant."""
    umbrales = ControlUmbralesStore(config)

    def crear_recursos(session: AsyncSession) -> Recursos:
        deps = Deps(
            ventas=VentaService(SqlVentasRepository(session)),
            caja=CajaService(SqlCajaRepository(session)),
            fiados=FiadosService(SqlFiadosRepository(session)),
            clientes=ClientesService(SqlClientesRepository(session)),
            # Cierre fiscal de mostrador (ADR 0012 D2): atado a la sesión del turno; encola la emisión POS
            # tras commitear (enqueue perezoso por `redis_url`). Inerte si la empresa no tiene el flag.
            cierre_pos=CierrePos(session),
        )
        return Recursos(
            deps=deps,
            catalogo=CatalogoDesdeVentas(SqlVentasRepository(session)),
            umbrales=umbrales,
        )

    return crear_recursos


def crear_bypass_factory(dispatcher: Dispatcher) -> Callable[[AsyncSession], Bypass]:
    """Devuelve `crear_bypass(session)` → `Bypass` cuyo catálogo (capa exacta) sale del repo de
    inventario de ESA sesión del tenant (DB-per-tenant). Default real del seam `crear_bypass` de
    `crear_turno_handler`; el match converge en el MISMO `dispatcher.ejecutar` que el modelo."""

    def crear_bypass(session: AsyncSession) -> Bypass:
        catalogo = CatalogoBypassExacto(SqlInventarioRepository(session), SqlVentasRepository(session))
        return Bypass(catalogo, dispatcher)

    return crear_bypass


def construir_deps(
    settings=None,
    *,
    abrir_control: ControlSession | None = None,
    abrir_tenant: SesionTenant | None = None,
    dedup: DedupStore | None = None,
    confirm: ConfirmStore | None = None,
    pendientes: VentaPendienteStore | None = None,
    recursos: RecursosBot | None = None,
) -> BotDeps:
    """Ensambla `BotDeps` desde los puertos reales; los seams en None toman su default real."""
    settings = settings or get_settings()
    master = settings.secrets_master_key
    abrir_control = abrir_control or control_session
    abrir_tenant = abrir_tenant or asynccontextmanager(tenant_session)
    dedup = dedup or RedisDedupStore(url=settings.redis_url)
    confirm = confirm or RedisConfirmStore(url=settings.redis_url)
    pendientes = pendientes or RedisVentaPendienteStore(url=settings.redis_url)
    recursos = recursos or RecursosBotImpl(cargar=_crear_cargar(abrir_control, master))

    config = ConfigControl(abrir_control)
    dispatcher = Dispatcher(
        config_store=config,
        key_store=KeyControl(abrir_control, master),
        plataforma=PlataformaLLM.desde_settings(settings),
    )
    crear_recursos = _crear_recursos_factory(config)
    procesar = crear_turno_handler(
        dispatcher=dispatcher,
        memoria=lambda s: MemoriaService(SqlMemoriaRepository(s)),
        costos=lambda s: SqlCostosRepository(s),
        crear_recursos=crear_recursos,
        recursos=recursos,
        confirm=confirm,
        crear_bypass=crear_bypass_factory(dispatcher),
        pendientes=pendientes,
        turno=Turno.WORKER,
    )
    procesar_callback = crear_callback_handler(
        dispatcher=dispatcher,
        pendientes=pendientes,
        crear_recursos=crear_recursos,
        memoria=lambda s: MemoriaService(SqlMemoriaRepository(s)),
        confirm=confirm,   # fail-closed: confirmación de venta sobre límite por botón (camino bypass)
    )
    return BotDeps(
        resolver=ResolverControl(abrir_control),
        secretos=SecretosControl(abrir_control, master),
        capacidades=CapacidadesControl(abrir_control),
        dedup=dedup,
        abrir_sesion=abrir_tenant,
        usuarios=lambda s: SqlUsuariosBotRepo(s),
        recursos=recursos,
        procesar=procesar,
        procesar_callback=procesar_callback,
        rubro=RubroControl(abrir_control),
    )
