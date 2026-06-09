"""Auth de plataforma del super-admin (ADR 0010 §D2). Sin red ni BD.

- Login de un super_admin → JWT con `scope=platform` y SIN claim `tenant`; `usuario.tenant` = None.
- INVARIANTE DE SEGURIDAD: un JWT de plataforma NUNCA resuelve un tenant. Contra una ruta /api de tenant
  (sin subdominio/header) el TenantMiddleware no resuelve empresa → la rechaza (404), nunca 200.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from apps.api.main import create_app
from core.auth import create_platform_token, decode_token
from core.auth.passwords import hash_password
from core.tenancy.identidades_repo import Identidad
from modules.auth.login_email import get_directorio, get_lockout, router

_HASH_OK = hash_password("clave-super-admin")


def _ident_super(*, activo: bool = True) -> Identidad:
    # Identidad de PLATAFORMA: empresa_id None, usuario_id centinela 0, rol super_admin.
    return Identidad(
        id=1, email="andres@ferrebot.co", password_hash=_HASH_OK,
        empresa_id=None, usuario_id=0, rol="super_admin", activo=activo,
    )


class _FakeDirectorio:
    def __init__(self, identidades: list[Identidad]) -> None:
        self._by_email = {i.email: i for i in identidades}

    async def buscar(self, email: str) -> Identidad | None:
        return self._by_email.get(email.strip().lower())

    async def slug_empresa(self, empresa_id: int) -> str | None:
        raise AssertionError("super_admin NO debe resolver slug de empresa")  # invariante


class _FakeLockout:
    def __init__(self) -> None:
        self.reseteos: list[str] = []

    async def bloqueado(self, clave: str) -> bool:
        return False

    async def registrar_fallo(self, clave: str) -> None:  # pragma: no cover - no se espera fallo aquí
        pass

    async def reset(self, clave: str) -> None:
        self.reseteos.append(clave)


def _app_login(directorio: _FakeDirectorio, lockout: _FakeLockout) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_directorio] = lambda: directorio
    app.dependency_overrides[get_lockout] = lambda: lockout
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_login_superadmin_emite_jwt_de_plataforma_sin_tenant():
    lockout = _FakeLockout()
    app = _app_login(_FakeDirectorio([_ident_super()]), lockout)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/login/password",
                         json={"email": "Andres@FerreBot.CO", "password": "clave-super-admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usuario"] == {"id": 0, "rol": "super_admin", "tenant": None}
    claims = decode_token(body["token"])
    assert claims["scope"] == "platform"
    assert "tenant" not in claims                 # SIN claim tenant: no ata a ninguna empresa
    assert claims["sub"] == "0" and claims["rol"] == "super_admin"
    assert lockout.reseteos == ["andres@ferrebot.co"]


async def test_jwt_de_plataforma_no_resuelve_tenant_en_ruta_de_tenant():
    # INVARIANTE: el token de plataforma (sin claim tenant) no resuelve empresa → ruta /api de tenant
    # rechazada por el middleware (404), jamás 200. App real con TenantMiddleware (no toca BD: sin slug).
    app = create_app()
    token = create_platform_token(user_id=0, rol="super_admin")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
    ) as c:
        r = await c.get("/api/v1/ventas", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code != 200
    assert r.status_code in (401, 403, 404)
