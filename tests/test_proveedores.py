"""Cuentas por pagar a proveedor + foto (Fase 12, Slice 4b) por HTTP contra base efímera real.

Patrón test_compras: app mínima + ASGITransport + overrides de auth y sesión del tenant (commit).
Cubre: factura nace pendiente, abono recalcula el saldo, abonos que saldan → 'pagada', dedup 409,
404/422 del abono, resumen, admin-only, y la foto (con un fake de Cloudinary → URL; sin Cloudinary →
503). NUNCA hay red real.
"""
from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from core.db.session import get_tenant_db
from modules.pagar.repository import SqlPagarRepository
from modules.pagar.service import PagarService
from modules.proveedores.router import get_cloudinary_client, router as proveedores_router


class _FakeCloud:
    """Cliente Cloudinary falso: NO toca red; devuelve una URL determinística."""

    def __init__(self) -> None:
        self.subidas: list[tuple[bytes, str | None]] = []

    async def subir(self, data: bytes, *, filename: str | None = None) -> str:
        self.subidas.append((data, filename))
        return f"https://res.cloudinary.test/{filename or 'soporte'}"


def _app(tenant, *, user_id: int, rol: str = "admin", cloud="DEFECTO") -> FastAPI:
    app = FastAPI()
    app.include_router(proveedores_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})  # router POS (ADR 0008)
    if cloud != "DEFECTO":   # None = empresa sin Cloudinary (503); o un _FakeCloud
        app.dependency_overrides[get_cloudinary_client] = lambda: cloud
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed_usuario(s: AsyncSession, *, rol: str = "admin") -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Quien', :r) RETURNING id"), {"r": rol})
    ).scalar_one()


def _factura(**over) -> dict:
    base = {"id": "FAC-001", "proveedor": "Ferre Mayorista", "total": 100000, "fecha": "2026-06-05"}
    base.update(over)
    return base


# ---- Facturas / abonos -----------------------------------------------------
async def test_crear_factura_nace_pendiente(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/proveedores/facturas", json=_factura())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body == {
        "id": "FAC-001", "proveedor": "Ferre Mayorista", "descripcion": None,
        "total": "100000.00", "pagado": "0.00", "pendiente": "100000.00",
        "estado": "pendiente", "fecha": "2026-06-05", "fecha_vencimiento": None,
        "foto_url": None, "foto_nombre": None,
    }


async def test_abono_recalcula_pendiente(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        r = await c.post("/api/v1/proveedores/abonos", json={"factura_id": "FAC-001", "monto": 30000, "fecha": "2026-06-06"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pagado"] == "30000.00"
    assert body["pendiente"] == "70000.00"
    assert body["estado"] == "pendiente"


async def test_abonos_que_saldan_marcan_pagada(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        await c.post("/api/v1/proveedores/abonos", json={"factura_id": "FAC-001", "monto": 60000, "fecha": "2026-06-06"})
        r = await c.post("/api/v1/proveedores/abonos", json={"factura_id": "FAC-001", "monto": 40000, "fecha": "2026-06-07"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pagado"] == "100000.00"
    assert body["pendiente"] == "0.00"
    assert body["estado"] == "pagada"


async def test_factura_id_duplicado_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/proveedores/facturas", json=_factura())
        r2 = await c.post("/api/v1/proveedores/facturas", json=_factura(proveedor="Otro"))
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


async def test_abono_factura_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/proveedores/abonos", json={"factura_id": "NO-EXISTE", "monto": 1000, "fecha": "2026-06-06"})
    assert r.status_code == 404, r.text


async def test_abono_excede_pendiente_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        r = await c.post("/api/v1/proveedores/abonos", json={"factura_id": "FAC-001", "monto": 150000, "fecha": "2026-06-06"})
    assert r.status_code == 422, r.text


async def test_abono_monto_no_positivo_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        r = await c.post("/api/v1/proveedores/abonos", json={"factura_id": "FAC-001", "monto": 0, "fecha": "2026-06-06"})
    assert r.status_code == 422, r.text   # Field(gt=0) lo rechaza


# ---- Proveedores registrados (desplegable del modal de producto) -----------
async def test_listar_proveedores_registrados(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.execute(
            text(
                "INSERT INTO proveedores (nombre, nit) VALUES "
                "('Zeta Ferre', '900.3'), ('Andina', '900.1'), ('Beta', NULL)"
            )
        )
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/proveedores")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["nombre"] for p in body] == ["Andina", "Beta", "Zeta Ferre"]  # ordenado por nombre
    andina = next(p for p in body if p["nombre"] == "Andina")
    assert andina["nit"] == "900.1" and isinstance(andina["id"], int)
    assert next(p for p in body if p["nombre"] == "Beta")["nit"] is None


async def test_listar_proveedores_es_solo_admin_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s, rol="vendedor")
        await s.commit()

    app = _app(tenant, user_id=uid, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/proveedores")
    assert r.status_code == 403, r.text


# ---- Resumen / listado -----------------------------------------------------
async def test_resumen_suma_pendientes(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura(id="A", total=100000))
        await c.post("/api/v1/proveedores/facturas", json=_factura(id="B", total=50000))
        await c.post("/api/v1/proveedores/abonos", json={"factura_id": "A", "monto": 30000, "fecha": "2026-06-06"})
        # Saldar B → sale del adeudado.
        await c.post("/api/v1/proveedores/abonos", json={"factura_id": "B", "monto": 50000, "fecha": "2026-06-06"})
        resumen = await c.get("/api/v1/proveedores/resumen")
        pendientes = await c.get("/api/v1/proveedores/facturas", params={"estado": "pendiente"})
    assert resumen.status_code == 200, resumen.text
    assert resumen.json() == {"total_adeudado": "70000.00", "facturas_pendientes": 1}  # solo A (70000)
    assert [f["id"] for f in pendientes.json()] == ["A"]


async def test_cxp_es_solo_admin_vendedor_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s, rol="vendedor")
        await s.commit()

    app = _app(tenant, user_id=uid, rol="vendedor")
    async with _cliente(app) as c:
        post = await c.post("/api/v1/proveedores/facturas", json=_factura())
        lista = await c.get("/api/v1/proveedores/facturas")
        resumen = await c.get("/api/v1/proveedores/resumen")
    assert post.status_code == 403, post.text
    assert lista.status_code == 403, lista.text
    assert resumen.status_code == 403, resumen.text


# ---- Foto (Cloudinary gateado) ---------------------------------------------
async def test_subir_foto_guarda_url_con_fake_cloudinary(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    fake = _FakeCloud()
    app = _app(tenant, user_id=uid, cloud=fake)
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        r = await c.post(
            "/api/v1/proveedores/facturas/FAC-001/foto",
            files={"file": ("soporte.jpg", b"\xff\xd8\xff datos", "image/jpeg")},
        )
    assert r.status_code == 200, r.text
    assert r.json()["foto_url"] == "https://res.cloudinary.test/soporte.jpg"
    assert len(fake.subidas) == 1

    async with AsyncSession(tenant.engine) as s:
        url = (await s.execute(text("SELECT foto_url FROM facturas_proveedores WHERE id='FAC-001'"))).scalar_one()
        assert url == "https://res.cloudinary.test/soporte.jpg"


async def test_subir_foto_sin_cloudinary_503(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid, cloud=None)   # empresa sin Cloudinary configurado
    async with _cliente(app) as c:
        await c.post("/api/v1/proveedores/facturas", json=_factura())
        r = await c.post(
            "/api/v1/proveedores/facturas/FAC-001/foto",
            files={"file": ("soporte.jpg", b"datos", "image/jpeg")},
        )
    assert r.status_code == 503, r.text


# ---- fecha_vencimiento (pack_pagar, opcional + backward-compatible) ---------
async def test_crear_factura_persiste_fecha_vencimiento(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    async with _cliente(_app(tenant, user_id=uid)) as c:
        r = await c.post(
            "/api/v1/proveedores/facturas",
            json=_factura(fecha="2026-06-05", fecha_vencimiento="2026-07-05"),
        )
        assert r.status_code == 201, r.text
        assert r.json()["fecha_vencimiento"] == "2026-07-05"
        lista = await c.get("/api/v1/proveedores/facturas")   # se persiste y vuelve en el listado
    assert lista.json()[0]["fecha_vencimiento"] == "2026-07-05"


async def test_crear_factura_sin_vencimiento_queda_null(tenant):
    """Backward-compatible: sin el campo, la factura entra con vencimiento NULL (comportamiento actual)."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    async with _cliente(_app(tenant, user_id=uid)) as c:
        r = await c.post("/api/v1/proveedores/facturas", json=_factura())
    assert r.status_code == 201 and r.json()["fecha_vencimiento"] is None


async def test_vencimiento_anterior_a_fecha_es_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    async with _cliente(_app(tenant, user_id=uid)) as c:
        r = await c.post(
            "/api/v1/proveedores/facturas",
            json=_factura(fecha="2026-06-05", fecha_vencimiento="2026-06-01"),
        )
    assert r.status_code == 422, r.text


async def test_motor_pagar_usa_fecha_vencimiento_capturada(tenant):
    """End-to-end: el vencimiento capturado en proveedores manda en el motor de pagar — el vencimiento
    efectivo es el capturado, NO el derivado (`fecha + plazo_default_dias`, que daría hoy+30: lejano)."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    hoy = today_co()
    vence = hoy + timedelta(days=2)
    async with _cliente(_app(tenant, user_id=uid)) as c:
        r = await c.post("/api/v1/proveedores/facturas", json=_factura(
            fecha=hoy.isoformat(), fecha_vencimiento=vence.isoformat()))
        assert r.status_code == 201, r.text

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cuentas = await PagarService(SqlPagarRepository(s)).cuentas_por_pagar(hoy)
    cuenta = next(x for x in cuentas if x.factura_id == "FAC-001")
    assert cuenta.vencimiento_efectivo == vence            # usa la capturada, no la derivada
    assert cuenta.dias_para_vencer == 2 and cuenta.por_vencer and not cuenta.vencida


async def test_aislamiento_factura_con_vencimiento_no_cruza_tenant(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    async with AsyncSession(empresa_a.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    async with _cliente(_app(empresa_a, user_id=uid)) as c:
        r = await c.post(
            "/api/v1/proveedores/facturas",
            json=_factura(fecha_vencimiento="2026-07-05"),
        )
        assert r.status_code == 201, r.text
    async with _cliente(_app(empresa_b, user_id=1)) as c:
        lista_b = await c.get("/api/v1/proveedores/facturas")
    assert lista_b.json() == []   # B nunca ve la factura (ni su vencimiento) de A
