"""Set-password + reset por token (modules/auth/password_reset, ADR 0009 A1.3). Sin red ni BD.

App mínima + ASGITransport + dependency_overrides: token store y repo se inyectan como fakes. Cubre:
set-password con token válido fija una clave verificable; token usado/expirado → 400; reset/solicitar
genera token (email existe) y permite cambiar la clave; reset de email inexistente → 200 genérico SIN
enumeración; política mínima de contraseña (422) sin consumir el token.
"""
from __future__ import annotations

import secrets

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth.passwords import verify_password
from core.tenancy.identidades_repo import Identidad
from modules.auth.password_reset import (
    get_repo_identidades,
    get_token_store,
    router,
)


def _ident(con_password: bool = False) -> Identidad:
    return Identidad(
        id=1, email="ana@clinica.co", password_hash="$argon2id$x" if con_password else None,
        empresa_id=7, usuario_id=42, rol="admin", activo=True,
    )


class _FakeTokenStore:
    """Single-use en memoria: `consumir` saca el token (no reusable). TTL ignorado (no se testea aquí)."""

    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}

    async def crear(self, identidad_id: int, ttl_segundos: int) -> str:
        token = secrets.token_urlsafe(16)
        self.tokens[token] = identidad_id
        return token

    async def consumir(self, token: str) -> int | None:
        return self.tokens.pop(token, None)   # un solo uso

    def sembrar(self, token: str, identidad_id: int) -> None:
        self.tokens[token] = identidad_id


class _FakeRepo:
    def __init__(self, identidades: list[Identidad]) -> None:
        self._by_email = {i.email: i for i in identidades}
        self.hashes: dict[int, str] = {}            # identidad_id -> hash fijado

    async def buscar_por_email(self, email: str) -> Identidad | None:
        return self._by_email.get(email.strip().lower())

    async def set_password_hash(self, identidad_id: int, password_hash: str) -> None:
        self.hashes[identidad_id] = password_hash


def _app(store: _FakeTokenStore, repo: _FakeRepo) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_token_store] = lambda: store
    app.dependency_overrides[get_repo_identidades] = lambda: repo
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


# --- set-password ------------------------------------------------------------

async def test_set_password_con_token_valido_fija_clave_verificable():
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    store.sembrar("tok-1", 42)
    app = _app(store, repo)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/set-password", json={"token": "tok-1", "password": "clave-nueva-1"})
    assert r.status_code == 200, r.text
    # El hash fijado verifica la nueva clave (luego el login funcionaría).
    assert verify_password("clave-nueva-1", repo.hashes[42]) is True
    assert store.tokens == {}                                   # token consumido


async def test_token_reusado_o_invalido_da_400():
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    store.sembrar("tok-1", 42)
    app = _app(store, repo)
    async with _cliente(app) as c:
        ok = await c.post("/api/v1/auth/set-password", json={"token": "tok-1", "password": "clave-nueva-1"})
        reuso = await c.post("/api/v1/auth/set-password", json={"token": "tok-1", "password": "otra-clave-9"})
        inexistente = await c.post("/api/v1/auth/set-password", json={"token": "no-existe", "password": "clave-nueva-1"})
    assert ok.status_code == 200
    assert reuso.status_code == 400 and inexistente.status_code == 400   # un solo uso / token inválido


async def test_password_corta_no_consume_token_422():
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    store.sembrar("tok-1", 42)
    app = _app(store, repo)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/set-password", json={"token": "tok-1", "password": "corta"})
    assert r.status_code == 422                       # política mínima (longitud)
    assert store.tokens == {"tok-1": 42}              # token NO consumido (validación previa al handler)
    assert repo.hashes == {}


# --- reset -------------------------------------------------------------------

async def test_reset_solicitar_email_existente_genera_token_y_permite_cambiar():
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    app = _app(store, repo)
    async with _cliente(app) as c:
        sol = await c.post("/api/v1/auth/reset/solicitar", json={"email": "ANA@clinica.co"})
        assert sol.status_code == 200
        assert len(store.tokens) == 1                 # se generó un token (email existe)
        token = next(iter(store.tokens))
        conf = await c.post("/api/v1/auth/reset/confirmar", json={"token": token, "password": "reseteada-7"})
    assert conf.status_code == 200
    assert verify_password("reseteada-7", repo.hashes[1]) is True


async def test_reset_solicitar_email_inexistente_200_sin_enumerar():
    store, repo = _FakeTokenStore(), _FakeRepo([])      # nadie
    app = _app(store, repo)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/auth/reset/solicitar", json={"email": "nadie@otra.co"})
    assert r.status_code == 200                          # mismo 200 genérico que si existiera
    assert store.tokens == {}                            # pero NO se generó ningún token
