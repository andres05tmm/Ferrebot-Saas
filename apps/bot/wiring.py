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

import base64
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession

from ai.bypass import Bypass
from ai.confirmacion import ConfirmStore, VentaPendienteStore
from ai.dispatcher import Dispatcher, Recursos
from ai.obra_tools import ContextoTelegram, ObraDeps, ResolverVision
from ai.ports import CatalogoDesdeVentas, ControlUmbralesStore
from ai.tools import Deps
from ai.turno import PrepararRecibo, crear_callback_handler, crear_turno_handler
from apps.bot.catalogo import CatalogoBypassExacto
from apps.bot.ports import BotDeps, DedupStore, RecursosBot, SesionTenant, UpdateBot
from apps.bot.recursos import Credenciales
from apps.bot.recursos import RecursosBot as RecursosBotImpl
from apps.bot.redis_stores import RedisConfirmStore, RedisDedupStore, RedisVentaPendienteStore
from apps.bot.repos import ControlCapacidades, ControlSecretosBot, SqlUsuariosBotRepo
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.llm.base import ImageBlock
from core.llm.factory import LLMResuelto, PlataformaLLM, Turno
from core.llm.gobierno import Gobierno, PoliticaGobierno, RedisGobierno
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from core.logging import get_logger
from core.tenancy.cache import control_cache
from core.tenancy.config_empresa import cargar_rubro
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_slug
from modules.caja.config import cargar_caja_obligatoria
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.clientes.repository import SqlClientesRepository
from modules.clientes.service import ClientesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.inventario.repository import SqlInventarioRepository
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.service import MaquinariaService
from modules.memoria.repository import SqlCostosRepository, SqlMemoriaRepository
from modules.memoria.service import MemoriaService
from modules.obra.repository import SqlObrasRepository
from modules.obra.service import ObrasService
from modules.facturacion.pos_hook import CierrePos
from modules.proveedores.cloudinary_client import CloudinaryClient
from modules.proveedores.cloudinary_config import cargar_config_cloudinary
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService

log = get_logger("bot.wiring")

# Abre una sesión del control DB (CM) por llamada. En prod = core.db.session.control_session.
ControlSession = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Prompt sintético cuando la foto del recibo llega SIN leyenda: el modelo sabe que debe registrar el
# gasto por foto (la imagen ya viaja por `recursos.obra.canal`, no como argumento).
_TEXTO_RECIBO_DEFAULT = "El usuario envió la foto de un comprobante de pago. Regístralo."


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


class CajaObligatoriaControl:
    """Toggle `caja_obligatoria` (guard de caja del POS) por empresa, per-call sobre el control DB
    (mismo patrón que RubroControl). Lo consume el handler `registrar_venta` del bot vía `Deps`."""

    def __init__(self, abrir_control: ControlSession) -> None:
        self._abrir = abrir_control

    async def activa(self, empresa_id: int) -> bool:
        async with self._abrir() as s:
            return await cargar_caja_obligatoria(s, empresa_id)


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


def _crear_recursos_factory(
    config: ConfigControl,
    resolver_vision: ResolverVision,
    caja_obligatoria: CajaObligatoriaControl | None = None,
) -> Callable[[AsyncSession], Recursos]:
    """Devuelve `crear_recursos(session)` → `Recursos` FRESCO por turno (servicios atados a esa sesión).
    Los umbrales salen del control (config_empresa), NO de la sesión del tenant.

    Adjunta el pack de obra (`Recursos.obra`) del Bot PIM: servicios de maquinaria/obra/caja atados a la
    sesión del turno + `resolver_vision` (del factory de `core/llm`) + canal VACÍO (la foto la inyecta
    `preparar_recibo` por turno). Se adjunta SIEMPRE: el gateo por capacidad (`obras`/`maquinaria`) lo
    hace el despachador —un tenant retail no ve ni ejecuta estas herramientas—, y construir los servicios
    es solo envolver repos (sin I/O). `cartera=None`: el cargo a cartera de alquiler exige la capacidad
    `cartera_alquiler`, que este factory no conoce por turno → seam no-op (wiring capability-aware queda
    pendiente, ver informe)."""
    umbrales = ControlUmbralesStore(config)

    def crear_recursos(session: AsyncSession) -> Recursos:
        fiados = FiadosService(SqlFiadosRepository(session))
        # `caja` se comparte con el pack de obra: el gasto por recibo del bot mueve la MISMA caja del turno.
        caja = CajaService(SqlCajaRepository(session))
        deps = Deps(
            # `fiados` compartido: una venta con metodo_pago=fiado crea su cargo en el ledger
            # dentro de la misma transacción (VentaService.registrar_venta).
            ventas=VentaService(SqlVentasRepository(session), fiados=fiados),
            caja=caja,
            fiados=fiados,
            clientes=ClientesService(SqlClientesRepository(session)),
            # Cierre fiscal de mostrador (ADR 0012 D2): atado a la sesión del turno; encola la emisión POS
            # tras commitear (enqueue perezoso por `redis_url`). Inerte si la empresa no tiene el flag.
            cierre_pos=CierrePos(session),
            # Guard de caja del POS (toggle `caja_obligatoria`, control DB): paridad con el API.
            caja_obligatoria=(caja_obligatoria.activa if caja_obligatoria is not None else None),
        )
        obra = ObraDeps(
            maquinaria=MaquinariaService(SqlMaquinasRepository(session)),
            obras=ObrasService(SqlObrasRepository(session)),
            caja=caja,
            resolver_vision=resolver_vision,
            canal=ContextoTelegram(),   # vacío; la foto la inyecta `preparar_recibo` por turno
        )
        return Recursos(
            deps=deps,
            catalogo=CatalogoDesdeVentas(SqlVentasRepository(session)),
            umbrales=umbrales,
            obra=obra,
        )

    return crear_recursos


def crear_preparar_recibo(
    recursos: RecursosBot, abrir_control: ControlSession, master: str
) -> PrepararRecibo:
    """Adaptador de canal de la FOTO del recibo (Bot PIM). Descarga la imagen de Telegram con el bot-token
    de la empresa, la sube al bucket (Cloudinary de esa empresa, si está configurado) y la inyecta en
    `recursos.obra.canal` como `ContextoTelegram` (la imagen NUNCA viaja como arg del modelo). El
    `telegram_message_id` es el ancla de idempotencia del gasto.

    FAIL-OPEN por diseño: un fallo de descarga deja el canal sin imagen (la tool pedirá la foto) y un
    fallo/ausencia de Cloudinary deja `comprobante_url=None` (la visión igual lee la imagen embebida en
    base64); nunca tumba el turno. Solo actúa para tenants con el pack de obra habilitado (capacidad
    `obras`); para el resto es no-op."""

    async def _cloud_client(empresa_id: int) -> CloudinaryClient | None:
        async with abrir_control() as cs:
            cred = await cargar_config_cloudinary(cs, master, empresa_id)
        return CloudinaryClient(cred) if cred is not None else None

    async def preparar_recibo(
        update: UpdateBot, ctx, recursos_turno: Recursos, texto: str | None
    ) -> tuple[Recursos, str | None]:
        if recursos_turno.obra is None or not ctx.tiene_capacidad("obras"):
            return recursos_turno, texto   # retail / sin el pack: la foto no se materializa
        if not update.foto_file_id:
            return recursos_turno, texto

        bundle = await recursos.para(ctx.tenant_id)
        try:
            data = await bundle.archivos.descargar(update.foto_file_id)
        except Exception:
            # Sin imagen descargada, la tool `registrar_gasto_recibo` responde "sin_imagen" (pide la foto).
            log.warning("bot_recibo_descarga_fallo", tenant_id=ctx.tenant_id, exc_info=True)
            return recursos_turno, texto or _TEXTO_RECIBO_DEFAULT

        imagen = ImageBlock.desde_base64(base64.b64encode(data).decode("ascii"), "image/jpeg")
        mensaje_id = update.telegram_message_id or update.update_id
        comprobante_url: str | None = None
        try:
            cloud = await _cloud_client(ctx.tenant_id)
            if cloud is not None:
                comprobante_url = await cloud.subir(data, filename=f"recibo-{mensaje_id}.jpg")
        except Exception:
            # El bucket es opcional: seguimos con la imagen embebida (base64); el gasto no guarda URL.
            log.warning("bot_recibo_subida_fallo", tenant_id=ctx.tenant_id, exc_info=True)

        canal = ContextoTelegram(
            imagen=imagen,
            telegram_user_id=str(update.telegram_id),
            telegram_message_id=str(mensaje_id),   # ancla de idempotencia del gasto
            comprobante_url=comprobante_url,
        )
        recursos_turno = replace(recursos_turno, obra=replace(recursos_turno.obra, canal=canal))
        return recursos_turno, texto or _TEXTO_RECIBO_DEFAULT

    return preparar_recibo


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

    async def resolver_vision(tenant_id: int) -> LLMResuelto:
        """Resuelve el (proveedor + modelo) con VISIÓN de la empresa para `extraer_recibo` del Bot PIM.
        Turno ORQUESTADOR = el modelo capaz (con visión), con la resiliencia (retry/respaldo) del
        dispatcher; reusa la selección por empresa del control DB."""
        return await dispatcher.seleccionar_proveedor(tenant_id, turno=Turno.ORQUESTADOR)

    crear_recursos = _crear_recursos_factory(
        config, resolver_vision, caja_obligatoria=CajaObligatoriaControl(abrir_control)
    )
    # Gobierno de agentes (ADR 0024): compuertas Redis (rate-limit + presupuesto) por empresa. Inerte
    # mientras los límites de plataforma sean 0 y la empresa no los active en config_empresa (opt-in).
    gobierno = Gobierno(
        store=RedisGobierno(url=settings.redis_url),
        plataforma=PoliticaGobierno.desde_settings(settings),
        config_store=config,
    )
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
        gobierno=gobierno,
        # Adaptador de canal de la foto del recibo (Bot PIM): descarga+bucket+canal en `recursos.obra`.
        preparar_recibo=crear_preparar_recibo(recursos, abrir_control, master),
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
