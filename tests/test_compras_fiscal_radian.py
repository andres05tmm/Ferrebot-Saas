"""RADIAN-FE recibidas (Fase 12, Slice 6b) — eventos DIAN sobre compras fiscales. MATIAS 100% FAKE.

⚠️ Son acciones DIAN reales en producción: aquí MATIAS está completamente mockeado (CERO red, CERO
DIAN). Dos niveles: (1) el `MatiasClient` por HTTP con `httpx.MockTransport` (pin del contrato §14, sin
red ni al construir); (2) el router+servicio REALES contra Postgres efímero con un cliente MATIAS FAKE
inyectado por `get_radian_deps`. Cubre: importar→030/pendiente, aceptar→032+033/aceptada,
reclamar→031/reclamada, idempotencia del 030, error de MATIAS→evento_error+502, gate 404, admin 403, 404.
"""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import now_co
from core.db.session import get_tenant_db
from modules.compras_fiscal.router import RadianDeps, get_radian_deps, router as compras_fiscal_router
from modules.facturacion.matias_client import (
    MatiasClient,
    MatiasCredenciales,
    EventoResultado,
    _parsear_evento,
)

_CRED = MatiasCredenciales(email="bot@empresa.co", password="secreto", base_url="https://matias.test/api")


# ---- Cliente MATIAS por HTTP (MockTransport, cero red) ----------------------
def test_parsear_evento():
    assert _parsear_evento({"success": True}).ok is True
    fallo = _parsear_evento({"success": False, "message": "No", "errors": {"cufe": "invalido"}})
    assert fallo.ok is False and "No" in fallo.error_msg and "cufe: invalido" in fallo.error_msg


class _Handler:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "JWT"})
        if "/events/" in request.url.path:
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={})


def _client(handler: _Handler) -> MatiasClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_CRED.base_url)
    return MatiasClient(_CRED, client=http)


async def test_importar_y_enviar_evento_perezoso_sin_red():
    handler = _Handler()
    cli = _client(handler)
    assert handler.paths == []                          # construir NO toca la red (CR-1)
    r1 = await cli.importar_track_id("CUFE123")
    r2 = await cli.enviar_evento("CUFE123", "030", "Acuse")
    assert r1.ok and r2.ok
    assert any(p.endswith("/events/import-track-id") for p in handler.paths)
    assert any(p.endswith("/events/send/CUFE123") for p in handler.paths)


# ---- Router + servicio REALES, MATIAS FAKE (Postgres efímero) ---------------
class _FakeMatias:
    """Cliente MATIAS falso: NO toca red; registra llamadas y puede simular fallo en un evento."""

    def __init__(self, *, fallar_en: str | None = None, explota: bool = False) -> None:
        self.calls: list[tuple] = []
        self._fallar_en = fallar_en   # "import" | "030" | "031" | "032" | "033" | None
        self._explota = explota

    async def importar_track_id(self, cufe: str) -> EventoResultado:
        self.calls.append(("import", cufe))
        if self._explota:
            raise RuntimeError("transporte caído")
        if self._fallar_en == "import":
            return EventoResultado(False, error_msg="track id rechazado por la DIAN")
        return EventoResultado(True)

    async def enviar_evento(self, cufe: str, code: str, notes: str = "") -> EventoResultado:
        self.calls.append((code, cufe, notes))
        if self._fallar_en == code:
            return EventoResultado(False, error_msg=f"evento {code} rechazado por la DIAN")
        return EventoResultado(True)


def _app(tenant, *, fake: _FakeMatias, rol: str = "admin", feature: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(compras_fiscal_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    caps = frozenset({"compras_fiscal"}) if feature else frozenset()
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: caps
    # FAKE de MATIAS: cero red, cero DIAN.
    app.dependency_overrides[get_radian_deps] = lambda: RadianDeps(matias=fake, ambiente="pruebas")
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed_fiscal(s: AsyncSession, *, nit: str = "900111") -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO compras_fiscal (proveedor_nit, base, iva, total, creado_en) "
                "VALUES (:n, 84033.61, 15966.39, 100000, :f) RETURNING id"
            ),
            {"n": nit, "f": now_co()},
        )
    ).scalar_one()


async def test_importar_envia_030_y_estado_pendiente(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-PROV-001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cufe_proveedor"] == "CUFE-PROV-001"
    assert body["evento_030_at"] is not None
    assert body["evento_estado"] == "pendiente"
    assert body["evento_error"] is None
    assert ("import", "CUFE-PROV-001") in fake.calls
    assert any(call[0] == "030" for call in fake.calls)

    async with AsyncSession(tenant.engine) as s:
        cufe, e030, estado = (
            await s.execute(text("SELECT cufe_proveedor, evento_030_at, evento_estado FROM compras_fiscal WHERE id=:i"), {"i": fid})
        ).one()
        assert cufe == "CUFE-PROV-001" and e030 is not None and estado == "pendiente"


async def test_importar_es_idempotente_no_reacusa(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-1"})
        r2 = await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-1"})
    assert r2.status_code == 200, r2.text
    # El segundo importar NO vuelve a llamar a MATIAS (ya tiene 030).
    assert sum(1 for call in fake.calls if call[0] == "import") == 1


async def test_aceptar_envia_032_y_033_y_estado_aceptada(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-2"})
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evento_032_at"] is not None and body["evento_033_at"] is not None
    assert body["evento_estado"] == "aceptada"
    assert [call[0] for call in fake.calls if call[0] in ("032", "033")] == ["032", "033"]


async def test_reclamar_envia_031_y_estado_reclamada(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-3"})
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/reclamar", json={"motivo": "Mercancía no recibida"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evento_031_at"] is not None and body["evento_estado"] == "reclamada"
    evento_031 = next(call for call in fake.calls if call[0] == "031")
    assert evento_031[2] == "Mercancía no recibida"   # el motivo viaja como notes


async def test_error_matias_persiste_evento_error_y_502(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    fake = _FakeMatias(fallar_en="030")   # importa el track id pero el acuse 030 es rechazado
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-X"})
    assert r.status_code == 502, r.text          # status limpio, no rompe
    assert "030" in r.json()["evento_error"]

    async with AsyncSession(tenant.engine) as s:
        error, e030, estado = (
            await s.execute(text("SELECT evento_error, evento_030_at, evento_estado FROM compras_fiscal WHERE id=:i"), {"i": fid})
        ).one()
        assert error is not None and "030" in error   # el error SÍ se persistió
        assert e030 is None and estado is None         # no quedó acusada


async def test_importar_fiscal_inexistente_404(tenant):
    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/compras-fiscal/999999/importar", json={"cufe": "CUFE-Z"})
    assert r.status_code == 404, r.text
    assert fake.calls == []                       # ni siquiera se llamó a MATIAS


async def test_radian_sin_feature_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    app = _app(tenant, fake=_FakeMatias(), feature=False)
    async with _cliente(app) as c:
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "C"})
    assert r.status_code == 404, r.text


async def test_radian_es_solo_admin_vendedor_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s)
        await s.commit()

    app = _app(tenant, fake=_FakeMatias(), rol="vendedor")
    async with _cliente(app) as c:
        r = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
    assert r.status_code == 403, r.text


async def test_retry_de_aceptar_no_reenvia_032(tenant):
    """Idempotencia parcial del par 032+033: si el 033 falla, `evento_032_at` ya quedó persistido
    y el reintento envía SOLO el 033 — un evento DIAN real jamás se duplica."""
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s, nit="900222")
        await s.commit()

    fallido = _FakeMatias(fallar_en="033")
    app = _app(tenant, fake=fallido)
    async with _cliente(app) as c:
        await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-R1"})
        r1 = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
    assert r1.status_code == 502, r1.text
    assert r1.json()["evento_032_at"] is not None   # el 032 exitoso quedó persistido

    reintento = _FakeMatias()
    app2 = _app(tenant, fake=reintento)
    async with _cliente(app2) as c:
        r2 = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
    assert r2.status_code == 200, r2.text
    assert r2.json()["evento_estado"] == "aceptada"
    codigos = [call[0] for call in reintento.calls]
    assert "032" not in codigos and codigos.count("033") == 1


async def test_aceptar_una_aceptada_es_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        fid = await _seed_fiscal(s, nit="900333")
        await s.commit()

    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        await c.post(f"/api/v1/compras-fiscal/{fid}/importar", json={"cufe": "CUFE-R2"})
        r1 = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
        r2 = await c.post(f"/api/v1/compras-fiscal/{fid}/aceptar")
        r3 = await c.post(f"/api/v1/compras-fiscal/{fid}/reclamar", json={"motivo": "tarde"})
    assert r1.status_code == 200
    assert r2.status_code == 409   # re-aceptar reenviaría eventos DIAN reales
    assert r3.status_code == 409   # reclamar una aceptada contradice el evento ya enviado
    assert [call[0] for call in fake.calls if call[0] in ("031", "032", "033")] == ["032", "033"]
