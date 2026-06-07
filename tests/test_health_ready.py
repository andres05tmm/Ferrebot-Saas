"""TDD de /health (liveness) y /ready (readiness con chequeo de dependencias).

/health es estático (200 ok, no toca dependencias). /ready ejecuta `chequear_listo` —función pura
testeable que recibe el pool ARQ y un proveedor de sesión de control— y devuelve 503 si algo falla.

Se testea la función directo (con dobles que lanzan al hacer SELECT 1 / ping) y los endpoints por
HTTP inyectando el resultado vía dependency_overrides (en tests no corre el lifespan).
"""
import httpx
from httpx import ASGITransport

from apps.api.main import ResultadoListo, chequear_listo, create_app, evaluar_listo


# --- dobles de dependencias --------------------------------------------------

class _FakeSession:
    def __init__(self, fallar: bool) -> None:
        self._fallar = fallar

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def execute(self, _stmt):
        if self._fallar:
            raise RuntimeError("control db caída")
        return None


class _FakeSessionmaker:
    """Espeja `_control()`: invocable que devuelve una sesión usable con `async with`."""

    def __init__(self, fallar: bool = False) -> None:
        self._fallar = fallar

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._fallar)


class _FakePool:
    def __init__(self, fallar: bool = False) -> None:
        self._fallar = fallar

    async def ping(self) -> bool:
        if self._fallar:
            raise RuntimeError("redis caído")
        return True


# --- función pura ------------------------------------------------------------

async def test_chequear_listo_todo_ok():
    res = await chequear_listo(_FakePool(), _FakeSessionmaker())
    assert res.listo is True
    assert res.checks == {"control_db": "ok", "redis": "ok"}


async def test_chequear_listo_control_caido():
    res = await chequear_listo(_FakePool(), _FakeSessionmaker(fallar=True))
    assert res.listo is False
    assert res.checks["control_db"] != "ok"
    assert res.checks["redis"] == "ok"


async def test_chequear_listo_redis_caido():
    res = await chequear_listo(_FakePool(fallar=True), _FakeSessionmaker())
    assert res.listo is False
    assert res.checks["redis"] != "ok"
    assert res.checks["control_db"] == "ok"


# --- endpoints por HTTP ------------------------------------------------------

def _cliente(app) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_health_siempre_ok():
    app = create_app()
    async with _cliente(app) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_health_acepta_head():
    # UptimeRobot (free) solo pinguea con HEAD: /health debe responder 200 (no 405).
    app = create_app()
    async with _cliente(app) as c:
        r = await c.head("/health")
    assert r.status_code == 200


async def test_ready_ok_200():
    app = create_app()
    app.dependency_overrides[evaluar_listo] = lambda: ResultadoListo(
        listo=True, checks={"control_db": "ok", "redis": "ok"}
    )
    async with _cliente(app) as c:
        r = await c.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "checks": {"control_db": "ok", "redis": "ok"}}


async def test_ready_fallo_503():
    app = create_app()
    app.dependency_overrides[evaluar_listo] = lambda: ResultadoListo(
        listo=False, checks={"control_db": "error", "redis": "ok"}
    )
    async with _cliente(app) as c:
        r = await c.get("/ready")
    assert r.status_code == 503
