"""Factory: resuelve (proveedor + modelo + key) para una empresa y un tipo de turno.

Precedencia (ADR 0005): override de `config_empresa` del tenant → default de plataforma (.env).
La key sale de `secretos_empresa` (por empresa) con fallback al .env de plataforma; nunca se
hardcodea. El umbral worker/orquestador escala el modelo en turnos multi-paso.

El factory depende de dos puertos (`ConfigStore`, `KeyStore`) para no acoplarse al control DB;
sus implementaciones reales viven en `core.llm.stores`. Así el despachador es testeable con fakes.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.llm import registry
from core.llm.base import LLMProvider, LLMSinCredencial

# Override por empresa (claves en config_empresa).
_CLAVE_PROVIDER = "llm_provider"
_CLAVE_MODELO_WORKER = "llm_model_worker"
_CLAVE_MODELO_ORQUESTADOR = "llm_model_orquestador"


class Turno(str, Enum):
    """Tipo de turno: worker (frecuente, modelo barato) u orquestador (multi-paso, modelo capaz)."""
    WORKER = "worker"
    ORQUESTADOR = "orquestador"


@dataclass(frozen=True, slots=True)
class PlataformaLLM:
    """Defaults de plataforma (del .env). `keys`: proveedor → API key de plataforma."""
    provider: str
    model_worker: str
    model_orquestador: str
    keys: dict[str, str]
    # Resiliencia (ADR 0023): retry apagable en caliente + proveedor de respaldo opcional (con sus
    # modelos, porque los nombres de modelo no son intercambiables entre vendors). "" = sin respaldo.
    retry_habilitado: bool = True
    fallback_provider: str = ""
    fallback_model_worker: str = ""
    fallback_model_orquestador: str = ""

    @classmethod
    def desde_settings(cls, settings) -> "PlataformaLLM":
        return cls(
            provider=settings.llm_provider,
            model_worker=settings.llm_model_worker,
            model_orquestador=settings.llm_model_orquestador,
            keys={"openai": settings.openai_api_key, "claude": settings.anthropic_api_key},
            retry_habilitado=settings.llm_retry_habilitado,
            fallback_provider=settings.llm_fallback_provider,
            fallback_model_worker=settings.llm_fallback_model_worker,
            fallback_model_orquestador=settings.llm_fallback_model_orquestador,
        )


@dataclass(frozen=True, slots=True)
class LLMResuelto:
    """Proveedor instanciado (con su key) + el modelo a usar para este turno."""
    provider: LLMProvider
    model: str
    provider_nombre: str


class ConfigStore(Protocol):
    async def overrides(self, empresa_id: int) -> dict[str, str]: ...


class KeyStore(Protocol):
    async def api_key(self, empresa_id: int, provider: str) -> str | None: ...


async def get_llm(
    empresa_id: int,
    *,
    turno: Turno = Turno.WORKER,
    config_store: ConfigStore,
    key_store: KeyStore,
    plataforma: PlataformaLLM,
) -> LLMResuelto:
    overrides = await config_store.overrides(empresa_id)

    provider_nombre = overrides.get(_CLAVE_PROVIDER) or plataforma.provider
    if turno is Turno.ORQUESTADOR:
        model = overrides.get(_CLAVE_MODELO_ORQUESTADOR) or plataforma.model_orquestador
    else:
        model = overrides.get(_CLAVE_MODELO_WORKER) or plataforma.model_worker

    clase = registry.obtener_clase(provider_nombre)
    key = await key_store.api_key(empresa_id, provider_nombre) or plataforma.keys.get(provider_nombre)
    if not key:
        raise LLMSinCredencial(empresa_id, provider_nombre)

    return LLMResuelto(
        provider=clase(api_key=key), model=model, provider_nombre=provider_nombre
    )


async def get_llm_con_fallback(
    empresa_id: int,
    *,
    turno: Turno = Turno.WORKER,
    config_store: ConfigStore,
    key_store: KeyStore,
    plataforma: PlataformaLLM,
) -> LLMResuelto:
    """`get_llm` + resiliencia (ADR 0023): retry con backoff y, si está configurado, respaldo.

    Con `retry_habilitado=False` devuelve el proveedor pelado (kill-switch en caliente por .env).
    El respaldo solo se cablea si `fallback_provider` apunta a OTRO proveedor con key disponible;
    usa sus propios modelos (`fallback_model_*`) porque los nombres no cruzan entre vendors.
    """
    base = await get_llm(
        empresa_id, turno=turno, config_store=config_store,
        key_store=key_store, plataforma=plataforma,
    )
    if not plataforma.retry_habilitado:
        return base

    from core.llm.resiliencia import ProveedorResiliente

    respaldo = None
    modelo_respaldo = None
    nombre_fb = plataforma.fallback_provider
    if nombre_fb and nombre_fb != base.provider_nombre:
        key_fb = await key_store.api_key(empresa_id, nombre_fb) or plataforma.keys.get(nombre_fb)
        modelo_respaldo = (
            plataforma.fallback_model_orquestador if turno is Turno.ORQUESTADOR
            else plataforma.fallback_model_worker
        )
        if key_fb and modelo_respaldo:
            respaldo = registry.obtener_clase(nombre_fb)(api_key=key_fb)

    return LLMResuelto(
        provider=ProveedorResiliente(
            base.provider, respaldo=respaldo,
            modelo_respaldo=modelo_respaldo if respaldo else None,
        ),
        model=base.model,
        provider_nombre=base.provider_nombre,
    )
