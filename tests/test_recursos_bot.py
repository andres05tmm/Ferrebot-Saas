"""CR-3a — caché de recursos del bot por empresa (`RecursosBot`), con fakes: cero red, cero SQL.

Pin del contrato (espejo de `EngineCache`):
  - `para(empresa)` resuelve las credenciales con `cargar` y arma el bundle de los TRES adaptadores
    de CR-1 (TelegramNotificador / WhisperTranscriptor / TelegramArchivos), atados a esa empresa;
  - dos llamadas para la MISMA empresa devuelven el bundle cacheado y `cargar` corre UNA sola vez;
  - empresas distintas → bundles distintos, `cargar` por cada una;
  - bajo concurrencia para la misma empresa, el lock serializa: `cargar` se llama una sola vez.
"""
import asyncio

from apps.bot.recursos import Credenciales, RecursosBot, RecursosEmpresa
from apps.bot.telegram import TelegramArchivos, TelegramNotificador
from core.voz.transcriptor import WhisperTranscriptor


# --------------------------------- fakes ----------------------------------

class CargadorFake:
    """`cargar` falso: devuelve credenciales pre-cargadas por empresa y cuenta cada invocación."""

    def __init__(self, creds_por_empresa: dict[int, Credenciales]):
        self._creds = creds_por_empresa
        self.llamadas: list[int] = []          # empresa_ids, en orden de carga

    async def __call__(self, empresa_id: int) -> Credenciales:
        self.llamadas.append(empresa_id)
        return self._creds[empresa_id]


def _creds(n: int) -> Credenciales:
    return Credenciales(bot_token=f"tok-{n}", openai_key=f"oai-{n}")


# ---------------------------------- tests ---------------------------------

async def test_para_construye_los_tres_adaptadores_de_la_empresa():
    cargador = CargadorFake({1: _creds(1)})
    recursos = RecursosBot(cargar=cargador)

    bundle = await recursos.para(1)

    assert isinstance(bundle, RecursosEmpresa)
    assert isinstance(bundle.notificador, TelegramNotificador)
    assert isinstance(bundle.transcriptor, WhisperTranscriptor)
    assert isinstance(bundle.archivos, TelegramArchivos)


async def test_misma_empresa_cachea_el_bundle_y_carga_una_vez():
    cargador = CargadorFake({1: _creds(1)})
    recursos = RecursosBot(cargar=cargador)

    b1 = await recursos.para(1)
    b2 = await recursos.para(1)

    assert b1 is b2                            # el bundle se cachea por empresa
    assert cargador.llamadas == [1]           # `cargar` corre una sola vez


async def test_empresas_distintas_tienen_bundles_distintos():
    cargador = CargadorFake({1: _creds(1), 2: _creds(2)})
    recursos = RecursosBot(cargar=cargador)

    b1 = await recursos.para(1)
    b2 = await recursos.para(2)

    assert b1 is not b2
    assert cargador.llamadas == [1, 2]        # `cargar` por cada empresa


async def test_para_concurrente_misma_empresa_carga_una_vez():
    # El lock serializa: dos `para(1)` simultáneas comparten un solo `cargar` (como EngineCache).
    evento = asyncio.Event()
    llamadas: list[int] = []

    async def cargar_lento(empresa_id: int) -> Credenciales:
        llamadas.append(empresa_id)
        await evento.wait()                   # retiene la primera carga dentro del lock
        return _creds(empresa_id)

    recursos = RecursosBot(cargar=cargar_lento)
    t1 = asyncio.create_task(recursos.para(1))
    await asyncio.sleep(0)                     # t1 entra al lock y queda en `cargar_lento`
    t2 = asyncio.create_task(recursos.para(1))
    await asyncio.sleep(0)                     # t2 queda esperando el lock
    evento.set()
    b1, b2 = await asyncio.gather(t1, t2)

    assert b1 is b2
    assert llamadas == [1]                     # una sola carga pese a la concurrencia
