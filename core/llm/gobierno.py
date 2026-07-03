"""Gobierno de agentes (ADR 0024): rate-limit por (empresa, usuario) + presupuesto diario por empresa.

Antes de gastar una llamada al modelo, el turno pasa por dos compuertas ATÓMICAS en Redis:

  - **Rate-limit** por (tenant, usuario): ventana fija — clave `llm:rl:{tenant}:{usuario}`.
  - **Presupuesto diario** por empresa: cada turno RESERVA un costo estimado contra un tope diario —
    clave `llm:budget:{tenant}:{fecha}` (fecha en TZ Colombia, regla #4).

Cada compuerta es un ÚNICO `EVAL` (Lua) → atómica aun bajo concurrencia (`asyncio.gather`): el
contador del presupuesto JAMÁS sobrepasa el tope (reserva-o-rechaza en el mismo script). Al exceder
cualquiera, el turno se CORTA con un mensaje amable al usuario (nunca en silencio, nunca una excepción
que escale a 500). El presupuesto se mide en TOKENS estimados: la reserva pre-llamada acota el gasto de
forma determinista y `core/llm/medicion.py::ProveedorMedido` reconcilia con el uso real del turno.

Defaults de plataforma en `core/config/settings.py`; override por empresa en `config_empresa` (mismo
`ConfigStore` que el factory); kill-switch en caliente (`gobierno_habilitado`) — mismo patrón que la
resiliencia F0 (ADR 0023). Aislamiento por tenant: las claves llevan el `tenant_id` → la empresa A
jamás toca el contador de la B (invariante crítico, test-primero).

Resiliencia: un fallo de Redis NO tumba el turno — la compuerta es un guardrail, no correctitud;
fail-open + log (coherente con la medición best-effort). El cliente Redis es PEREZOSO e inyectable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from core.config.timezone import today_co
from core.llm.factory import ConfigStore
from core.logging import get_logger

log = get_logger("core.llm.gobierno")

# TTL de la clave del presupuesto diario: 48 h. La `fecha` en la clave ya reinicia el contador cada
# día Colombia; el TTL solo garantiza que la clave no viva para siempre (y cubre la reconciliación
# nocturna tardía). No hace falta alinear al fin del día exacto.
_TTL_PRESUPUESTO_S = 172_800

# Claves del override por empresa en config_empresa (texto plano, no secreto).
_CLAVE_RATE_LIMITE = "llm_rate_limite"
_CLAVE_RATE_VENTANA = "llm_rate_ventana_s"
_CLAVE_PRESUPUESTO = "llm_presupuesto_diario"

# Mensajes al usuario al cortar (amables y genéricos; español, canal-agnóstico).
MENSAJE_RATE = "Vas muy rápido. Espera unos segundos y vuelve a escribirme, por favor."
MENSAJE_PRESUPUESTO = (
    "Por hoy alcanzamos el límite de uso del asistente. Inténtalo de nuevo mañana o escríbele al "
    "administrador del negocio."
)


class Corte(str, Enum):
    """Motivo por el que el gobierno cortó el turno (para logs/observabilidad)."""

    RATE = "rate_limit"
    PRESUPUESTO = "presupuesto"


@dataclass(frozen=True, slots=True)
class Decision:
    """Veredicto del gobierno para un turno. `permitido=False` trae siempre un `mensaje` al usuario."""

    permitido: bool
    corte: Corte | None = None
    mensaje: str | None = None

    @classmethod
    def permitir(cls) -> "Decision":
        return cls(True)

    @classmethod
    def cortar(cls, corte: Corte, mensaje: str) -> "Decision":
        return cls(False, corte, mensaje)


def _entero(valor: Any, defecto: int) -> int:
    """Parseo defensivo de un override (texto de config_empresa) a int; `defecto` si no es válido."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


@dataclass(frozen=True, slots=True)
class PoliticaGobierno:
    """Parámetros de las compuertas. `0` en un límite = compuerta apagada (opt-in por tenant)."""

    habilitado: bool = True
    rate_limite: int = 0             # llamadas permitidas por ventana y usuario (0 = sin rate-limit)
    rate_ventana_s: int = 60
    presupuesto_diario: int = 0      # tope diario por empresa, en tokens estimados (0 = sin tope)
    costo_estimado_turno: int = 1500  # reserva de tokens por turno (gate pre-llamada)

    @classmethod
    def desde_settings(cls, settings: Any) -> "PoliticaGobierno":
        return cls(
            habilitado=settings.gobierno_habilitado,
            rate_limite=settings.gobierno_rate_limite,
            rate_ventana_s=settings.gobierno_rate_ventana_s,
            presupuesto_diario=settings.gobierno_presupuesto_diario,
            costo_estimado_turno=settings.gobierno_costo_estimado_turno,
        )

    def con_overrides(self, overrides: dict[str, str]) -> "PoliticaGobierno":
        """Aplica el override por empresa (config_empresa) sobre el default de plataforma.

        Solo se sobreescribe lo que la empresa configuró explícitamente; el resto conserva el default.
        El kill-switch de plataforma manda: si está apagado, ningún override lo reenciende.
        """
        from dataclasses import replace

        cambios: dict[str, int] = {}
        if _CLAVE_RATE_LIMITE in overrides:
            cambios["rate_limite"] = _entero(overrides[_CLAVE_RATE_LIMITE], self.rate_limite)
        if _CLAVE_RATE_VENTANA in overrides:
            cambios["rate_ventana_s"] = _entero(overrides[_CLAVE_RATE_VENTANA], self.rate_ventana_s)
        if _CLAVE_PRESUPUESTO in overrides:
            cambios["presupuesto_diario"] = _entero(
                overrides[_CLAVE_PRESUPUESTO], self.presupuesto_diario
            )
        return replace(self, **cambios) if cambios else self


class GobiernoStore(Protocol):
    """Puerto de las compuertas atómicas (lo implementa `RedisGobierno`; los tests lo falsean)."""

    async def permitir_rate(
        self, tenant_id: int, usuario_id: int | str, limite: int, ventana_s: int
    ) -> bool: ...

    async def reservar_presupuesto(
        self, tenant_id: int, fecha: str, costo: int, limite: int, ttl_s: int
    ) -> bool: ...


# Rate-limit de ventana fija: INCR + EXPIRE (solo en el 1er hit) → permitido si el contador <= límite.
# Un ÚNICO EVAL: atómico. Los rechazados también incrementan pero no reinician el TTL (ventana fija).
_LUA_RATE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
if n > tonumber(ARGV[1]) then return 0 end
return 1
"""

# Reserva de presupuesto: si `usado + costo` cabe en el límite, INCRBY y permite; si no, RECHAZA sin
# tocar el contador. Un ÚNICO EVAL → el contador NUNCA sobrepasa el límite, ni bajo concurrencia.
_LUA_PRESUPUESTO = """
local usado = tonumber(redis.call('GET', KEYS[1]) or '0')
local costo = tonumber(ARGV[1])
local limite = tonumber(ARGV[2])
if usado + costo > limite then return 0 end
local nuevo = redis.call('INCRBY', KEYS[1], costo)
if nuevo == costo then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3])) end
return 1
"""


class RedisGobierno:
    """Compuertas atómicas sobre Redis (Lua). Cliente perezoso e inyectable (patrón del bot/wa)."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    def _c(self) -> Any:
        return self._client or _cliente_redis(self._url)

    async def permitir_rate(
        self, tenant_id: int, usuario_id: int | str, limite: int, ventana_s: int
    ) -> bool:
        key = f"llm:rl:{tenant_id}:{usuario_id}"
        r = await self._c().eval(_LUA_RATE, 1, key, str(limite), str(ventana_s))
        return bool(int(r))

    async def reservar_presupuesto(
        self, tenant_id: int, fecha: str, costo: int, limite: int, ttl_s: int
    ) -> bool:
        key = f"llm:budget:{tenant_id}:{fecha}"
        r = await self._c().eval(_LUA_PRESUPUESTO, 1, key, str(costo), str(limite), str(ttl_s))
        return bool(int(r))

    async def ajustar_presupuesto(self, tenant_id: int, fecha: str, delta: int) -> None:
        """Reconcilia la reserva con el uso REAL del turno (delta = real − estimado). Best-effort.

        No es una compuerta: nunca rechaza. Un delta negativo devuelve reserva sobrante; uno positivo
        cobra el exceso, de modo que el contador diario refleje el consumo real medido en el borde.
        """
        if delta == 0:
            return
        key = f"llm:budget:{tenant_id}:{fecha}"
        await self._c().incrby(key, delta)


class Gobierno:
    """Orquesta las compuertas para un turno: kill-switch → política por empresa → rate → presupuesto.

    Orden: rate-limit ANTES del presupuesto (un turno frenado por frecuencia no consume presupuesto).
    Fail-open: un fallo de Redis se loguea y NO corta el turno (la compuerta es guardrail, no correctitud).
    """

    def __init__(
        self,
        *,
        store: GobiernoStore,
        plataforma: PoliticaGobierno,
        config_store: ConfigStore | None = None,
    ) -> None:
        self._store = store
        self._plataforma = plataforma
        self._config_store = config_store

    async def evaluar(self, tenant_id: int, usuario_id: int | str) -> Decision:
        if not self._plataforma.habilitado:            # kill-switch de plataforma (en caliente)
            return Decision.permitir()
        politica = await self._politica(tenant_id)
        if not politica.habilitado:
            return Decision.permitir()

        if politica.rate_limite > 0:
            if not await self._permitir_rate(tenant_id, usuario_id, politica):
                log.info("gobierno_corte_rate", tenant_id=tenant_id, usuario_id=usuario_id)
                return Decision.cortar(Corte.RATE, MENSAJE_RATE)

        if politica.presupuesto_diario > 0:
            if not await self._reservar(tenant_id, politica):
                log.info(
                    "gobierno_corte_presupuesto", tenant_id=tenant_id,
                    presupuesto=politica.presupuesto_diario,
                )
                return Decision.cortar(Corte.PRESUPUESTO, MENSAJE_PRESUPUESTO)

        return Decision.permitir()

    async def registrar_uso(self, tenant_id: int, tokens_reales: int) -> None:
        """Reconcilia la reserva del turno con el uso real (best-effort; jamás rompe el turno).

        Solo aplica si hay presupuesto activo y el store soporta el ajuste. `delta` = real − estimado.
        """
        politica = await self._politica(tenant_id)
        if politica.presupuesto_diario <= 0:
            return
        ajustar = getattr(self._store, "ajustar_presupuesto", None)
        if ajustar is None:
            return
        delta = int(tokens_reales) - politica.costo_estimado_turno
        try:
            await ajustar(tenant_id, today_co().isoformat(), delta)
        except Exception:
            log.warning("gobierno_reconciliar_fallo", tenant_id=tenant_id, exc_info=True)

    async def _politica(self, tenant_id: int) -> PoliticaGobierno:
        if self._config_store is None:
            return self._plataforma
        try:
            overrides = await self._config_store.overrides(tenant_id)
        except Exception:
            log.warning("gobierno_overrides_fallo", tenant_id=tenant_id, exc_info=True)
            return self._plataforma
        return self._plataforma.con_overrides(overrides)

    async def _permitir_rate(
        self, tenant_id: int, usuario_id: int, politica: PoliticaGobierno
    ) -> bool:
        try:
            return await self._store.permitir_rate(
                tenant_id, usuario_id, politica.rate_limite, politica.rate_ventana_s
            )
        except Exception:
            log.warning("gobierno_rate_fallo", tenant_id=tenant_id, exc_info=True)
            return True   # fail-open: un fallo de Redis no tumba el turno

    async def _reservar(self, tenant_id: int, politica: PoliticaGobierno) -> bool:
        try:
            return await self._store.reservar_presupuesto(
                tenant_id, today_co().isoformat(), politica.costo_estimado_turno,
                politica.presupuesto_diario, _TTL_PRESUPUESTO_S,
            )
        except Exception:
            log.warning("gobierno_presupuesto_fallo", tenant_id=tenant_id, exc_info=True)
            return True   # fail-open


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar, no al cargar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
