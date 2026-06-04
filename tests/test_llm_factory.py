"""Factory: resolución de proveedor + modelo + key con precedencia config_empresa → plataforma."""
import pytest

from core.llm.base import LLMSinCredencial
from core.llm.factory import PlataformaLLM, Turno, get_llm

_PLATAFORMA = PlataformaLLM(
    provider="openai",
    model_worker="gpt-4o-mini",
    model_orquestador="gpt-4o",
    keys={"openai": "sk-plataforma-oai", "claude": "sk-plataforma-claude"},
)


class _FakeConfigStore:
    def __init__(self, overrides: dict[int, dict[str, str]]):
        self._o = overrides

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        return self._o.get(empresa_id, {})


class _FakeKeyStore:
    def __init__(self, keys: dict[tuple[int, str], str]):
        self._k = keys

    async def api_key(self, empresa_id: int, provider: str) -> str | None:
        return self._k.get((empresa_id, provider))


def _factory(*, overrides=None, keys=None):
    return dict(
        config_store=_FakeConfigStore(overrides or {}),
        key_store=_FakeKeyStore(keys or {}),
        plataforma=_PLATAFORMA,
    )


async def test_default_plataforma_sin_override():
    res = await get_llm(1, turno=Turno.WORKER, **_factory(keys={(1, "openai"): "sk-e"}))
    assert res.provider_nombre == "openai"
    assert res.model == "gpt-4o-mini"


async def test_orquestador_escala_modelo():
    res = await get_llm(1, turno=Turno.ORQUESTADOR, **_factory(keys={(1, "openai"): "sk-e"}))
    assert res.model == "gpt-4o"


async def test_override_de_empresa_gana():
    cfg = {1: {"llm_provider": "claude", "llm_model_worker": "claude-haiku-x"}}
    res = await get_llm(
        1, turno=Turno.WORKER, **_factory(overrides=cfg, keys={(1, "claude"): "sk-e"})
    )
    assert res.provider_nombre == "claude"
    assert res.model == "claude-haiku-x"


async def test_key_de_empresa_gana_sobre_plataforma():
    res = await get_llm(1, turno=Turno.WORKER, **_factory(keys={(1, "openai"): "sk-empresa"}))
    assert res.provider.api_key == "sk-empresa"


async def test_key_cae_a_plataforma_si_empresa_no_tiene():
    res = await get_llm(1, turno=Turno.WORKER, **_factory(keys={}))
    assert res.provider.api_key == "sk-plataforma-oai"


async def test_sin_credencial_falla_explicito():
    plataforma = PlataformaLLM(
        provider="openai", model_worker="gpt-4o-mini", model_orquestador="gpt-4o", keys={}
    )
    with pytest.raises(LLMSinCredencial):
        await get_llm(
            1, turno=Turno.WORKER,
            config_store=_FakeConfigStore({}), key_store=_FakeKeyStore({}), plataforma=plataforma,
        )
