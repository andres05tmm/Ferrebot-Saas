"""Recepción de facturas de proveedor por QR (ADR 0020, F1). MATIAS 100% FAKE.

Cubre: (1) decodificación PURA del QR → CUFE (URL DIAN, campos, hash crudo, basura); (2) el flujo REAL
router+servicio contra Postgres efímero con MATIAS fakeado — crea soporte fiscal + cuenta por pagar +
acuse 030, degrada sin credenciales, y —invariantes críticos— es IDEMPOTENTE por CUFE (reescanear no
duplica) y AÍSLA por tenant (la empresa B nunca ve lo de A). Sin inventario (v1). Gate 404, admin 403,
QR basura 422.
"""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.compras_fiscal.errors import QRInvalido
from modules.compras_fiscal.qr import extraer_cufe
from modules.compras_fiscal.router import (
    RadianDeps,
    get_recepcion_deps,
    router as compras_fiscal_router,
)
from modules.facturacion.matias_client import EventoResultado

# CUFE de ejemplo (96 hex, SHA-384).
_CUFE = "a" * 96
_CUFE2 = "b" * 96


# ---- Decodificación PURA del QR → CUFE (sin DB, sin red) --------------------
def test_extraer_cufe_hash_crudo():
    assert extraer_cufe(_CUFE) == _CUFE
    assert extraer_cufe(f"  {_CUFE.upper()}  ") == _CUFE   # normaliza y recorta


def test_extraer_cufe_url_dian():
    url = f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={_CUFE}"
    assert extraer_cufe(url) == _CUFE
    # clave case-insensitive
    assert extraer_cufe(f"https://x/y?DocumentKey={_CUFE}&otro=1") == _CUFE


def test_extraer_cufe_campos_clave_valor():
    texto = f"NumFac: FE123\nFecFac: 2026-07-01\nCUFE: {_CUFE.upper()}\nValFac: 100000"
    assert extraer_cufe(texto) == _CUFE


@pytest.mark.parametrize("basura", ["", "   ", "no-hay-cufe-aqui", "1234", "https://dian.gov.co/x?y=1"])
def test_extraer_cufe_basura_lanza(basura):
    with pytest.raises(QRInvalido):
        extraer_cufe(basura)


# ---- Flujo REAL router + servicio, MATIAS FAKE -----------------------------
class _FakeMatias:
    """Cliente MATIAS falso: cero red; cuenta llamadas y puede simular fallo del acuse."""

    def __init__(self, *, fallar_en: str | None = None) -> None:
        self.calls: list[tuple] = []
        self._fallar_en = fallar_en

    async def importar_track_id(self, cufe: str) -> EventoResultado:
        self.calls.append(("import", cufe))
        if self._fallar_en == "import":
            return EventoResultado(False, error_msg="track id rechazado")
        return EventoResultado(True)

    async def enviar_evento(self, cufe: str, code: str, notes: str = "") -> EventoResultado:
        self.calls.append((code, cufe, notes))
        if self._fallar_en == code:
            return EventoResultado(False, error_msg=f"evento {code} rechazado")
        return EventoResultado(True)


def _app(tenant, *, fake=None, degradado=False, rol="admin", feature=True) -> FastAPI:
    """App con solo el router de compras fiscal; MATIAS fakeado (o None si `degradado`)."""
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
    # user_id=None → usuario_id NULL en la factura (la BD efímera no siembra usuarios; la FK admite NULL).
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=None, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: caps
    deps = None if degradado else RadianDeps(matias=fake or _FakeMatias(), ambiente="pruebas")
    app.dependency_overrides[get_recepcion_deps] = lambda: deps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


def _payload(cufe: str = _CUFE, **extra) -> dict:
    base = {
        "qr": f"https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey={cufe}",
        "proveedor_nit": "900123456",
        "proveedor_nombre": "Ferretería Central",
        "numero_factura": "FE-9001",
        "total": "119000",
        "fecha": "2026-07-01",
        "fecha_vencimiento": "2026-07-31",
    }
    base.update(extra)
    return base


async def test_escanear_crea_soporte_deuda_y_acusa_030(tenant):
    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cufe"] == _CUFE
    assert body["cuenta_por_pagar_id"] == _CUFE
    assert body["fecha_vencimiento"] == "2026-07-31"
    assert body["pendiente"] == "119000.00"
    assert body["evento_030_at"] is not None and body["evento_estado"] == "pendiente"
    assert ("import", _CUFE) in fake.calls
    assert any(call[0] == "030" for call in fake.calls)

    # Persistencia: 1 compra fiscal con CUFE + 1 cuenta por pagar con vencimiento real.
    async with AsyncSession(tenant.engine) as s:
        nf = (await s.execute(text("SELECT count(*) FROM compras_fiscal WHERE cufe_proveedor=:c"), {"c": _CUFE})).scalar_one()
        venc = (await s.execute(text("SELECT fecha_vencimiento FROM facturas_proveedores WHERE id=:c"), {"c": _CUFE})).scalar_one()
    assert nf == 1
    assert str(venc) == "2026-07-31"


async def test_escanear_es_idempotente_por_cufe(tenant):
    """Invariante crítico: reescanear el MISMO CUFE no duplica factura ni cuenta por pagar ni re-acusa."""
    fake = _FakeMatias()
    app = _app(tenant, fake=fake)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
        r2 = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text        # replay: no creó, devolvió el existente
    assert r1.json()["cufe"] == r2.json()["cufe"] == _CUFE

    async with AsyncSession(tenant.engine) as s:
        n_fiscal = (await s.execute(text("SELECT count(*) FROM compras_fiscal WHERE cufe_proveedor=:c"), {"c": _CUFE})).scalar_one()
        n_cxp = (await s.execute(text("SELECT count(*) FROM facturas_proveedores WHERE id=:c"), {"c": _CUFE})).scalar_one()
    assert n_fiscal == 1                          # sin duplicar el soporte fiscal
    assert n_cxp == 1                             # sin duplicar la deuda
    assert sum(1 for call in fake.calls if call[0] == "import") == 1   # MATIAS solo la primera vez


async def test_escanear_aisla_por_tenant(tenant_factory):
    """Invariante crítico: la factura recibida en la empresa A no aparece en la empresa B."""
    a = await tenant_factory()
    b = await tenant_factory()

    app_a = _app(a)
    async with _cliente(app_a) as c:
        r = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
    assert r.status_code == 201, r.text

    app_b = _app(b)
    async with _cliente(app_b) as c:
        lista_b = await c.get("/api/v1/facturas-recibidas")
    assert lista_b.status_code == 200
    assert lista_b.json() == []                   # B no ve nada de A

    async with _cliente(_app(a)) as c:
        la = await c.get("/api/v1/facturas-recibidas")
    assert len(la.json()) == 1 and la.json()[0]["cufe"] == _CUFE


async def test_escanear_degrada_sin_matias(tenant):
    """Sin credenciales MATIAS (deps None): registra deuda + soporte con CUFE, sin acuse (evento_estado NULL)."""
    app = _app(tenant, degradado=True)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cufe"] == _CUFE
    assert body["evento_030_at"] is None and body["evento_estado"] is None
    assert body["cuenta_por_pagar_id"] == _CUFE   # la deuda SÍ quedó registrada

    async with AsyncSession(tenant.engine) as s:
        cufe = (await s.execute(text("SELECT cufe_proveedor FROM compras_fiscal WHERE cufe_proveedor=:c"), {"c": _CUFE})).scalar_one()
    assert cufe == _CUFE


async def test_escanear_qr_basura_422(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload(qr="no-hay-cufe"))
    assert r.status_code == 422, r.text


async def test_listar_recibidas_compone_deuda(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
        await c.post("/api/v1/facturas-recibidas/escanear", json=_payload(cufe=_CUFE2, qr=_CUFE2, numero_factura="FE-9002"))
        r = await c.get("/api/v1/facturas-recibidas")
    assert r.status_code == 200, r.text
    cufes = {f["cufe"] for f in r.json()}
    assert cufes == {_CUFE, _CUFE2}
    assert all(f["cuenta_por_pagar_id"] == f["cufe"] for f in r.json())


async def test_recibidas_sin_feature_404(tenant):
    app = _app(tenant, feature=False)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/facturas-recibidas/escanear", json=_payload())
    assert r.status_code == 404, r.text


async def test_recibidas_solo_admin_403(tenant):
    app = _app(tenant, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/facturas-recibidas")
    assert r.status_code == 403, r.text
