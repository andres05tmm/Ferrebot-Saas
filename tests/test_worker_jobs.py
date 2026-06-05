"""E4b-2 RED — lógica pura del job de emisión (sin Redis ni runtime ARQ).

Pin del contrato: `_backoff` es exponencial acotado; `emitir_documento` traduce la `Decision`
(E4b-1) a la semántica del worker (Retry / dead_letter / terminal) sin propagar otra excepción.
En RED todos fallan por NotImplementedError.
"""
import arq
import pytest

from apps.worker.jobs import _backoff, emitir_documento
from modules.facturacion.politica import Decision


def test_backoff_exponencial():
    assert _backoff(1) == 30
    assert _backoff(2) == 60
    assert _backoff(3) == 120
    assert _backoff(20) == 3600          # tope


class _FakeServicio:
    """Servicio fake: su `emitir` devuelve la `Decision` pre-cargada."""

    def __init__(self, decision: Decision) -> None:
        self._decision = decision

    async def emitir(self, factura_id: int) -> Decision:
        return self._decision


def _ctx(decision: Decision, *, job_try: int = 1) -> dict:
    async def crear_servicio(_tid: int) -> _FakeServicio:
        return _FakeServicio(decision)

    return {"crear_servicio": crear_servicio, "job_try": job_try}


async def test_job_reintenta():
    ctx = _ctx(Decision("error", reintentar=True, dead_letter=False))
    with pytest.raises(arq.Retry):
        await emitir_documento(ctx, 1, 10)


async def test_job_dead_letter():
    ctx = _ctx(Decision("error", reintentar=False, dead_letter=True))
    assert await emitir_documento(ctx, 1, 10) == "dead_letter"


async def test_job_terminal_aceptada():
    ctx = _ctx(Decision("aceptada", reintentar=False, dead_letter=False))
    assert await emitir_documento(ctx, 1, 10) == "aceptada"
