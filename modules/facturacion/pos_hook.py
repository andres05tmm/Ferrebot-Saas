"""Cierre fiscal de la venta (ADR 0014): un único núcleo rutea POS / FE / nada, dos cableados.

Generaliza el cierre POS automático (ADR 0012 D2) para que el documento fiscal lo decida la
**capacidad del tenant** + una **intención opcional por venta**:

- `pos_electronico` ON, intención POS o None  → POS (FE a pedido).
- intención FE con `facturacion_electronica`  → FE (suprime el POS, exclusión D1); FE a consumidor final.
- tenant FE-only (`facturacion_electronica`, sin `pos_electronico`) → FE por defecto.
- sin capacidades fiscales → no se crea documento DIAN (la venta queda solo interna).

La intención se PLUMBEA como parámetro opcional (default None → default por capacidad); persistirla o
elegirla en la UI es una fase posterior. "No registrar ante DIAN" = ausencia de capacidades del tenant,
NUNCA una opción por venta.

El cierre se invoca en el **punto común post-registro de la venta** — el router HTTP (`/ventas`) y el
handler `_registrar_venta` del agente (convergencia de bypass/confirmación/modelo, canal principal del
mostrador). Contrato innegociable: **jamás rompe la venta** (un fallo del cierre se traga y loguea),
idempotente (`pos:{venta_id}` / `fe:{venta_id}`) y excluyente POS↔FE (D1).

Carrera commit↔encolado (fix de auditoría): el núcleo **commitea el pendiente ANTES de encolar**
`emitir_documento`. Si se encolara antes del commit, el worker podría correr `emitir()` sin que la fila
exista todavía (caería al reconciliador en vez del camino feliz). Commit-antes-de-encolar lo elimina.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.config_empresa import cargar_auto_facturar_venta
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import ConfigFiscal, FacturacionService

log = get_logger("facturacion.pos_hook")

FEATURE_POS = "pos_electronico"
FEATURE_FE = "facturacion_electronica"
JOB_EMITIR = "emitir_documento"

# Intención de documento por venta. None = default por capacidad del tenant (ver `_resolver_documento`).
IntencionDocumento = Literal["pos", "fe"]

Enqueue = Callable[..., Awaitable[Any]]
CargarConfig = Callable[[int], Awaitable[ConfigFiscal]]


def _resolver_documento(
    capacidades: frozenset[str], intencion: IntencionDocumento | None,
    auto_facturar: bool = True,
) -> IntencionDocumento | None:
    """Decide el documento fiscal de la venta: 'pos', 'fe' o None (PURO; fuente única del ruteo, ADR 0014).

    La intención explícita se respeta SOLO si el tenant tiene la capacidad correspondiente; si la pide
    sin tenerla, cae al default por capacidad (nunca emite lo que no puede). Default por capacidad
    (intención None): POS si hay `pos_electronico` (FE a pedido); FE si solo hay `facturacion_electronica`
    (FE-only); None si no hay capacidad fiscal (la venta queda solo interna, sin documento DIAN).

    `auto_facturar=False` (config del tenant `facturar_en_venta`): sin intención explícita, la venta NO
    auto-emite (queda interna; se factura a pedido con POST /facturas). Una intención explícita —POS o FE
    elegida en esa venta— SIEMPRE se respeta, así el toggle apaga solo el default automático."""
    tiene_pos = FEATURE_POS in capacidades
    tiene_fe = FEATURE_FE in capacidades
    if intencion == "fe" and tiene_fe:
        return "fe"
    if intencion == "pos" and tiene_pos:
        return "pos"
    if not auto_facturar:
        return None
    if tiene_pos:
        return "pos"
    if tiene_fe:
        return "fe"
    return None


async def cerrar_venta_fiscal(
    *, servicio: FacturacionService, session: AsyncSession, venta_id: int,
    tenant_id: int, capacidades: frozenset[str], enqueue: Enqueue,
    intencion: IntencionDocumento | None = None, auto_facturar: bool = True,
) -> int | None:
    """Núcleo del cierre fiscal. Rutea POS/FE/nada, crea el pendiente, **commitea** y luego encola.

    Devuelve `factura_id` o None. None = sin capacidad fiscal / auto-facturación apagada sin intención /
    excluido por documento existente (D1) / pendiente ya creado (no se re-encola: evita una segunda
    emisión y un segundo documento DIAN). El `commit` ocurre SOLO cuando se crea un pendiente nuevo, así
    que sin documento no altera la venta.

    Para FE el `servicio` DEBE traer `ConfigFiscal` (reserva consecutivo con `config.prefix`); para POS
    no hace falta (número/prefijo los asigna MATIAS, D4). El cableado carga la config solo cuando rutea FE."""
    documento = _resolver_documento(capacidades, intencion, auto_facturar)
    if documento is None:
        return None
    if documento == "pos":
        factura, creada = await servicio.crear_pendiente_pos(venta_id)
    else:
        factura, creada = await servicio.crear_pendiente_fe(venta_id)
    if not (creada and factura is not None):
        return None
    await session.commit()                       # commit ANTES de encolar: el worker ve la fila (sin carrera)
    await enqueue(JOB_EMITIR, tenant_id, factura.id)
    return factura.id


def _servicio(session: AsyncSession, config: ConfigFiscal | None = None) -> FacturacionService:
    return FacturacionService(SqlFacturacionRepository(session), config=config)


async def _cargar_config_tenant(tenant_id: int) -> ConfigFiscal:
    """Descifra la `ConfigFiscal` del tenant del control DB (solo para la rama FE, que necesita `prefix`)."""
    async with control_session() as cs:
        _cred, config = await cargar_config_matias(cs, get_settings().secrets_master_key, tenant_id)
    return config


async def _cargar_auto_facturar_tenant(tenant_id: int) -> bool:
    """Lee el toggle `facturar_en_venta` del tenant (control DB). Para el camino del bot.

    FALLA-ABIERTO: ante cualquier error del control DB devuelve True (default histórico: auto-facturar).
    Así un problema transitorio de config NUNCA suprime la facturación en silencio —el cierre del bot
    traga excepciones (jamás rompe la venta), y sin este catch un read fallido abortaría el cierre entero."""
    try:
        async with control_session() as cs:
            return await cargar_auto_facturar_venta(cs, tenant_id)
    except Exception:  # noqa: BLE001 — fail-open al comportamiento histórico
        log.warning("auto_facturar_config_fallo", tenant_id=tenant_id, exc_info=True)
        return True


# ── Enqueue perezoso para caminos sin pool ARQ inyectado (bot Telegram) ───────
_pool: Any = None
_pool_lock = asyncio.Lock()


async def _enqueue_lazy(job: str, *args: Any) -> None:
    """Encola en ARQ con un pool MEMOIZADO por proceso (creado del `redis_url`). Perezoso: no toca red al
    importar. Lo usa el cierre del bot, que no recibe el pool del lifespan del API."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                from arq import create_pool
                from arq.connections import RedisSettings
                _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await _pool.enqueue_job(job, *args)


class CierrePos:
    """Puerto de cierre fiscal para el agente (inyectado en `ai.tools.Deps`, cableado en `apps.bot.wiring`).

    Atado a la sesión del tenant del turno; las capacidades llegan del `Contexto` (ya resueltas/cacheadas),
    no se vuelve a pegar al control DB salvo en la rama FE (necesita la `ConfigFiscal`, carga perezosa).
    Nunca lanza: el cierre fiscal jamás tumba el registro de la venta."""

    def __init__(
        self, session: AsyncSession, *, enqueue: Enqueue | None = None,
        cargar_config: CargarConfig | None = None,
        cargar_auto_facturar: Callable[[int], Awaitable[bool]] | None = None,
    ) -> None:
        self._session = session
        self._enqueue = enqueue or _enqueue_lazy
        self._cargar_config = cargar_config or _cargar_config_tenant
        self._cargar_auto_facturar = cargar_auto_facturar or _cargar_auto_facturar_tenant

    async def cerrar(
        self, venta_id: int, *, tenant_id: int, capacidades: frozenset[str],
        intencion: IntencionDocumento | None = None,
    ) -> None:
        try:
            auto_facturar = await self._cargar_auto_facturar(tenant_id)
            documento = _resolver_documento(capacidades, intencion, auto_facturar)
            if documento is None:
                return
            config = await self._cargar_config(tenant_id) if documento == "fe" else None
            await cerrar_venta_fiscal(
                servicio=_servicio(self._session, config), session=self._session, venta_id=venta_id,
                tenant_id=tenant_id, capacidades=capacidades, enqueue=self._enqueue, intencion=intencion,
                auto_facturar=auto_facturar,
            )
        except Exception:  # noqa: BLE001 — el cierre fiscal jamás rompe el registro de la venta
            log.warning("cierre_fiscal_fallo", venta_id=venta_id, exc_info=True)


async def encolar_cierre_pos(
    request: Request, session: AsyncSession, venta_id: int,
    *, intencion: IntencionDocumento | None = None,
) -> None:
    """Cableado HTTP del cierre (router `/ventas`): capacidades del control DB + pool ARQ del lifespan.

    Resuelve tenant/pool del `request`; si faltan (apps mínimas de test) no hace nada. Carga la
    `ConfigFiscal` solo cuando rutea FE (la rama POS no la necesita). Nunca lanza."""
    tenant = getattr(request.state, "tenant", None)
    arq_pool = getattr(getattr(request.app, "state", None), "arq_pool", None)
    if tenant is None or arq_pool is None:
        return
    try:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(tenant.id)
            auto_facturar = await cargar_auto_facturar_venta(cs, tenant.id)
            documento = _resolver_documento(capacidades, intencion, auto_facturar)
            config = None
            if documento == "fe":
                _cred, config = await cargar_config_matias(
                    cs, get_settings().secrets_master_key, tenant.id
                )
        factura_id = await cerrar_venta_fiscal(
            servicio=_servicio(session, config), session=session, venta_id=venta_id,
            tenant_id=tenant.id, capacidades=capacidades, enqueue=arq_pool.enqueue_job, intencion=intencion,
            auto_facturar=auto_facturar,
        )
        if factura_id is not None:
            log.info("cierre_fiscal_encolado", tenant_id=tenant.id, venta_id=venta_id,
                     factura_id=factura_id, documento=documento)
    except Exception:  # noqa: BLE001 — el cierre fiscal jamás rompe el registro de la venta
        log.warning("cierre_fiscal_fallo", venta_id=venta_id, exc_info=True)
