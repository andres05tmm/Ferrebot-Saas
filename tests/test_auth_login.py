"""E1 — login del dashboard por Telegram Login Widget → JWT (Fase 11).

Dos planos:
  - Función PURA `verificar_widget` (modules/auth/telegram.py): firma HMAC-SHA256 con clave
    SHA256(bot_token) sobre el data_check_string + frescura (anti-replay). Sin red, sin BD.
  - Router por HTTP (patrón test_facturacion_router: app mínima + ASGITransport +
    dependency_overrides). El tenant resuelto, el lector de secretos (bot-token por empresa) y el
    mapeo telegram_id→usuario se inyectan como fakes: CERO red, CERO Postgres.

El firmado del payload en el test es una implementación INDEPENDIENTE del spec de Telegram (no
reusa la función de producción), así detecta bugs reales (no excluir `hash`, secret en hexdigest,
no ordenar, comparación errónea).
"""
from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from apps.bot.ports import UsuarioBot
from core.auth import create_access_token, decode_token, get_current_user
from core.config.timezone import now_co
from modules.auth.router import get_secretos, get_tenant, get_usuarios, router
from modules.auth.telegram import verificar_widget

_BOT_TOKEN = "123456:ABC-TestBotTokenXYZ"


# --- firmado independiente del widget (spec Telegram) ------------------------

def _firmar(datos: dict, bot_token: str = _BOT_TOKEN) -> str:
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(datos.items()) if k != "hash")
    secret = hashlib.sha256(bot_token.encode("utf-8")).digest()
    return hmac.new(secret, dcs.encode("utf-8"), hashlib.sha256).hexdigest()


def _payload(**over) -> dict:
    """Payload del widget firmado y fresco; `over` sobreescribe campos antes de firmar."""
    base: dict = {
        "id": 555,
        "first_name": "Ana",
        "username": "ana",
        "auth_date": int(now_co().timestamp()),
    }
    base.update(over)
    base["hash"] = _firmar(base)
    return base


# --- función pura ------------------------------------------------------------

def test_verificar_widget_ok():
    assert verificar_widget(_payload(), _BOT_TOKEN) is True


def test_verificar_widget_hash_manipulado():
    p = _payload()
    p["hash"] = "0" * 64                                # hash que no corresponde
    assert verificar_widget(p, _BOT_TOKEN) is False


def test_verificar_widget_otro_bot_token():
    assert verificar_widget(_payload(), "999999:OtroTokenDistinto") is False


def test_verificar_widget_auth_date_viejo():
    viejo = int(now_co().timestamp()) - 90_000          # > 86400 s → caduco
    assert verificar_widget(_payload(auth_date=viejo), _BOT_TOKEN) is False


# --- router por HTTP ---------------------------------------------------------

_TENANT = SimpleNamespace(id=7, slug="pr")


class _FakeSecretos:
    def __init__(self, token: str | None) -> None:
        self._token = token

    async def bot_token(self, empresa_id: int) -> str | None:
        return self._token

    async def webhook_secret(self, empresa_id: int) -> str | None:
        return None


class _FakeUsuarios:
    def __init__(self, usuario: UsuarioBot | None) -> None:
        self._u = usuario

    async def por_telegram_id(self, telegram_id: int) -> UsuarioBot | None:
        return self._u


def _app(*, token: str | None, usuario: UsuarioBot | None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_tenant] = lambda: _TENANT
    app.dependency_overrides[get_secretos] = lambda: _FakeSecretos(token)
    app.dependency_overrides[get_usuarios] = lambda: _FakeUsuarios(usuario)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_login_ok_emite_jwt_con_claims():
    admin = UsuarioBot(id=42, rol="admin", activo=True)
    app = _app(token=_BOT_TOKEN, usuario=admin)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/login", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usuario"] == {"id": 42, "rol": "admin", "tenant": "pr"}
    claims = decode_token(body["token"])
    assert claims["sub"] == "42"
    assert claims["tenant"] == "pr"
    assert claims["rol"] == "admin"


async def test_login_sin_usuario_401():
    app = _app(token=_BOT_TOKEN, usuario=None)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/login", json=_payload())
    assert r.status_code == 401


async def test_login_usuario_inactivo_401():
    inactivo = UsuarioBot(id=42, rol="vendedor", activo=False)
    app = _app(token=_BOT_TOKEN, usuario=inactivo)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/login", json=_payload())
    assert r.status_code == 401


async def test_login_hash_invalido_401():
    admin = UsuarioBot(id=42, rol="admin", activo=True)
    app = _app(token=_BOT_TOKEN, usuario=admin)
    payload = _payload()
    payload["hash"] = "0" * 64
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/login", json=payload)
    assert r.status_code == 401


async def test_me_devuelve_principal_del_token():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    token = create_access_token(user_id=42, tenant="pr", rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": 42, "rol": "admin", "tenant": "pr"}
