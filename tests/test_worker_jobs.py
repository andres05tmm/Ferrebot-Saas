"""E4b-2 RED — lógica pura del job de emisión (sin Redis ni runtime ARQ).

Pin del contrato: `_backoff` es exponencial acotado; `emitir_documento` traduce la `Decision`
(E4b-1) a la semántica del worker (Retry / dead_letter / terminal) sin propagar otra excepción.
En RED todos fallan por NotImplementedError.
"""
import arq
import pytest

from apps.worker.jobs import (
    _backoff,
    descargar_documento,
    emitir_documento,
    procesar_webhook_matias,
)
from modules.facturacion.politica import Decision


def test_backoff_exponencial():
    assert _backoff(1) == 30
    assert _backoff(2) == 60
    assert _backoff(3) == 120
    assert _backoff(20) == 3600          # tope


class _FakeRedis:
    """ArqRedis fake: registra los `enqueue_job(job, *args)` del worker."""

    def __init__(self) -> None:
        self.encolados: list[tuple] = []

    async def enqueue_job(self, job: str, *args) -> None:
        self.encolados.append((job, *args))


class _FakeServicio:
    """Servicio fake: `emitir` devuelve la `Decision` pre-cargada; `descargar_documento` un bool."""

    def __init__(self, decision: Decision | None = None, *, descarga_ok: bool = True) -> None:
        self._decision = decision
        self._descarga_ok = descarga_ok

    async def emitir(self, factura_id: int) -> Decision:
        return self._decision

    async def descargar_documento(self, factura_id: int) -> bool:
        return self._descarga_ok


def _ctx(decision: Decision, *, job_try: int = 1, redis=None) -> dict:
    async def crear_servicio(_tid: int) -> _FakeServicio:
        return _FakeServicio(decision)

    ctx = {"crear_servicio": crear_servicio, "job_try": job_try}
    if redis is not None:
        ctx["redis"] = redis
    return ctx


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


# --- D7.3: archivado del XML post-aceptada -----------------------------------

async def test_aceptada_encola_descarga_de_xml():
    redis = _FakeRedis()
    ctx = _ctx(Decision("aceptada", False, False), redis=redis)
    assert await emitir_documento(ctx, 7, 10) == "aceptada"
    assert redis.encolados == [("descargar_documento", 7, 10)]


async def test_aceptada_sin_redis_no_rompe():
    # En tests/smoke sin Redis el archivado simplemente no se encola (no debe lanzar).
    ctx = _ctx(Decision("aceptada", False, False))
    assert await emitir_documento(ctx, 7, 10) == "aceptada"


async def test_rechazada_no_encola_descarga():
    redis = _FakeRedis()
    ctx = _ctx(Decision("rechazada", False, False), redis=redis)
    assert await emitir_documento(ctx, 7, 10) == "rechazada"
    assert redis.encolados == []


def _ctx_descarga(*, descarga_ok: bool, job_try: int = 1) -> dict:
    async def crear_servicio(_tid: int) -> _FakeServicio:
        return _FakeServicio(descarga_ok=descarga_ok)

    return {"crear_servicio": crear_servicio, "job_try": job_try}


async def test_descargar_documento_ok():
    assert await descargar_documento(_ctx_descarga(descarga_ok=True), 7, 10) == "archivado"


async def test_descargar_documento_reintenta():
    with pytest.raises(arq.Retry):
        await descargar_documento(_ctx_descarga(descarga_ok=False), 7, 10)


# --- D7.1: procesamiento del webhook MATIAS en el worker ---------------------

class _ServicioWebhook:
    """Servicio fake: `procesar_webhook` devuelve la (accion, factura_id) pre-cargada."""

    def __init__(self, resultado: tuple) -> None:
        self._resultado = resultado

    async def procesar_webhook(self, recibido_id: int) -> tuple:
        return self._resultado


def _ctx_webhook(resultado: tuple, *, redis=None) -> dict:
    async def crear_servicio(_tid: int) -> _ServicioWebhook:
        return _ServicioWebhook(resultado)

    ctx = {"crear_servicio": crear_servicio, "job_try": 1}
    if redis is not None:
        ctx["redis"] = redis
    return ctx


async def test_webhook_aceptada_encola_descarga():
    redis = _FakeRedis()
    ctx = _ctx_webhook(("aceptada", 55), redis=redis)
    assert await procesar_webhook_matias(ctx, 7, 99) == "aceptada"
    assert redis.encolados == [("descargar_documento", 7, 55)]


async def test_webhook_rechazada_no_encola():
    redis = _FakeRedis()
    ctx = _ctx_webhook(("rechazada", 55), redis=redis)
    assert await procesar_webhook_matias(ctx, 7, 99) == "rechazada"
    assert redis.encolados == []
