"""Set-password + reset por token (modules/auth/password_reset, ADR 0009 A1.3). Sin red ni BD.

App mínima + ASGITransport + dependency_overrides: token store, repo y rate-limiter se inyectan como
fakes. Cubre: set-password con token válido fija una clave verificable; token usado/expirado → 400;
reset/solicitar genera token (email existe) y permite cambiar la clave; reset de email inexistente →
200 genérico SIN enumeración; política mínima de contraseña (422) sin consumir el token; rate-limit por
IP+email (429 tras N intentos, independiente por email y por IP); el token NUNCA aparece en los logs.
"""
from __future__ import annotations

import secrets

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from structlog.testing import capture_logs

from core.auth.passwords import verify_password
from core.tenancy.identidades_repo import Identidad
from modules.auth.password_reset import (
    get_rate_limiters,
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


class _FakeRateLimiter:
    """Cuenta por clave y bloquea pasados `max` intentos. Por defecto permisivo (no estorba)."""

    def __init__(self, max_intentos: int = 100) -> None:
        self._max = max_intentos
        self.counts: dict[str, int] = {}

    async def permitido(self, clave: str) -> bool:
        self.counts[clave] = self.counts.get(clave, 0) + 1   # sube SIEMPRE: no depende de si el email existe
        return self.counts[clave] <= self._max


def _app(
    store: _FakeTokenStore,
    repo: _FakeRepo,
    rl_email: _FakeRateLimiter | None = None,
    rl_ip: _FakeRateLimiter | None = None,
    rl_global: _FakeRateLimiter | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_token_store] = lambda: store
    app.dependency_overrides[get_repo_identidades] = lambda: repo
    # Tres cubos INDEPENDIENTES (email-solo / IP-sola / global); por defecto permisivos.
    el = rl_email or _FakeRateLimiter()
    il = rl_ip or _FakeRateLimiter()
    gl = rl_global or _FakeRateLimiter()
    app.dependency_overrides[get_rate_limiters] = lambda: (el, il, gl)
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


# --- rate-limit: dos cubos independientes (email-solo / IP-sola) -------------

async def test_reset_solicitar_email_bombing_aunque_rote_la_ip_dispara_429_por_cubo_email():
    # CLAVE: mismo email víctima desde IPs DISTINTAS (XFF rotado). El cubo de EMAIL ignora la IP, así
    # que el ataque de email-bombing dirigido se frena igual. (Con el cubo combinado anterior, cada
    # (ip,email) era un cubo nuevo → este caso NO disparaba y la víctima recibía N enlaces.)
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    rl_email = _FakeRateLimiter(max_intentos=2)                  # cubo email apretado
    rl_ip = _FakeRateLimiter(max_intentos=1000)                 # IP generosa: aquí no estorba
    app = _app(store, repo, rl_email=rl_email, rl_ip=rl_ip)
    async with _cliente(app) as c:
        codes = [
            (await c.post(
                "/api/v1/auth/reset/solicitar",
                json={"email": "ana@clinica.co"},
                headers={"x-forwarded-for": f"203.0.113.{i}"},   # IP NUEVA cada vez
            )).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]                             # frena por el cubo de email
    assert len(rl_email.counts) == 1                            # un solo cubo de email (la IP no lo parte)


async def test_reset_solicitar_abuso_por_ip_con_emails_distintos_dispara_429_por_cubo_ip():
    # Una sola IP enumerando/spameando MUCHOS emails distintos: el cubo de email nunca acumula, pero el
    # cubo de IP sí → se frena el abuso por IP.
    store, repo = _FakeTokenStore(), _FakeRepo([])               # da igual si existen
    rl_email = _FakeRateLimiter(max_intentos=1000)             # email generoso: aquí no estorba
    rl_ip = _FakeRateLimiter(max_intentos=2)                    # cubo IP apretado
    app = _app(store, repo, rl_email=rl_email, rl_ip=rl_ip)
    async with _cliente(app) as c:
        codes = [
            (await c.post(
                "/api/v1/auth/reset/solicitar", json={"email": f"v{i}@otra.co"}   # email NUEVO cada vez
            )).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]                             # frena por el cubo de IP
    assert len(rl_ip.counts) == 1                              # una sola IP (los emails no la parten)


async def test_reset_solicitar_429_no_enumera_mismo_resultado_exista_o_no_el_email():
    # Ambos cubos cuentan ANTES de tocar el directorio → 200/429 idénticos exista o no el email.
    rl_email_existe, rl_email_no = _FakeRateLimiter(max_intentos=2), _FakeRateLimiter(max_intentos=2)
    app_existe = _app(_FakeTokenStore(), _FakeRepo([_ident()]), rl_email=rl_email_existe)
    app_no = _app(_FakeTokenStore(), _FakeRepo([]), rl_email=rl_email_no)
    async with _cliente(app_existe) as c:
        existe = [
            (await c.post("/api/v1/auth/reset/solicitar", json={"email": "ana@clinica.co"})).status_code
            for _ in range(3)
        ]
    async with _cliente(app_no) as c:
        no_existe = [
            (await c.post("/api/v1/auth/reset/solicitar", json={"email": "ana@clinica.co"})).status_code
            for _ in range(3)
        ]
    assert existe == no_existe == [200, 200, 429]              # el límite no revela existencia


async def test_reset_solicitar_no_loguea_el_token_en_claro():
    store, repo = _FakeTokenStore(), _FakeRepo([_ident()])
    app = _app(store, repo)
    with capture_logs() as logs:
        async with _cliente(app) as c:
            r = await c.post("/api/v1/auth/reset/solicitar", json={"email": "ana@clinica.co"})
    assert r.status_code == 200
    secreto = next(iter(store.tokens))                        # el token realmente generado
    # El secreto no aparece en NINGÚN campo de NINGÚN log, ni en un campo llamado "token".
    assert all(e.get("token") is None for e in logs)
    assert not any(secreto in str(v) for e in logs for v in e.values())
