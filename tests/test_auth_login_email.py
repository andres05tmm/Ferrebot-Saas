"""Login real email/contraseña (modules/auth/login_email, ADR 0009 §D2/D4). Sin red ni BD.

App mínima + ASGITransport + dependency_overrides (patrón test_auth_login): el directorio (control DB)
y el lockout (Redis) se inyectan como fakes. Cubre: login ok → JWT con el claim `tenant` de la empresa
del usuario; SIN enumeración (email inexistente, clave errada, inactivo y sin-clave dan el MISMO 401);
y lockout (429) tras N fallos.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import decode_token
from core.auth.passwords import hash_password
from core.tenancy.identidades_repo import Identidad
from modules.auth.login_email import get_directorio, get_lockout, get_lockout_ip, router

_HASH_OK = hash_password("clave-correcta")


def _ident(*, activo: bool = True, con_password: bool = True) -> Identidad:
    return Identidad(
        id=1, email="ana@clinica.co", password_hash=_HASH_OK if con_password else None,
        empresa_id=7, usuario_id=42, rol="admin", activo=activo,
    )


class _FakeDirectorio:
    def __init__(self, identidades: list[Identidad], slug: str = "clinica") -> None:
        self._by_email = {i.email: i for i in identidades}   # emails ya normalizados
        self._slug = slug

    async def buscar(self, email: str) -> Identidad | None:
        return self._by_email.get(email.strip().lower())

    async def slug_empresa(self, empresa_id: int) -> str | None:
        return self._slug


class _FakeLockout:
    def __init__(self, max_intentos: int = 5) -> None:
        self.fallos: dict[str, int] = {}
        self.max = max_intentos
        self.reseteos: list[str] = []

    async def bloqueado(self, clave: str) -> bool:
        return self.fallos.get(clave, 0) >= self.max

    async def registrar_fallo(self, clave: str) -> None:
        self.fallos[clave] = self.fallos.get(clave, 0) + 1

    async def reset(self, clave: str) -> None:
        self.reseteos.append(clave)
        self.fallos.pop(clave, None)


def _app(
    directorio: _FakeDirectorio, lockout: _FakeLockout, lockout_ip: _FakeLockout | None = None
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_directorio] = lambda: directorio
    app.dependency_overrides[get_lockout] = lambda: lockout
    # Cubo por IP: permisivo por defecto (tope alto) para que los tests de email no lo pisen.
    li = lockout_ip or _FakeLockout(max_intentos=1000)
    app.dependency_overrides[get_lockout_ip] = lambda: li
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _login(app: FastAPI, email: str, password: str) -> httpx.Response:
    async with _cliente(app) as c:
        return await c.post("/api/v1/auth/login/password", json={"email": email, "password": password})


# --- camino feliz ------------------------------------------------------------

async def test_login_ok_emite_jwt_con_tenant_de_la_empresa():
    lockout = _FakeLockout()
    app = _app(_FakeDirectorio([_ident()]), lockout)
    # Email con otro casing: el login normaliza igual.
    r = await _login(app, "Ana@Clinica.CO", "clave-correcta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["usuario"] == {"id": 42, "rol": "admin", "tenant": "clinica"}
    claims = decode_token(body["token"])
    assert claims["tenant"] == "clinica" and claims["sub"] == "42" and claims["rol"] == "admin"
    assert lockout.reseteos == ["ana@clinica.co"]      # éxito limpia el contador de fallos


# --- sin enumeración de usuarios (mismo 401 para toda causa) -----------------

def _401(r: httpx.Response) -> tuple[int, dict]:
    return r.status_code, r.json()


async def test_clave_mala_y_email_inexistente_dan_la_misma_respuesta():
    app_ok = _app(_FakeDirectorio([_ident()]), _FakeLockout())
    app_vacio = _app(_FakeDirectorio([]), _FakeLockout())

    clave_mala = await _login(app_ok, "ana@clinica.co", "incorrecta")
    inexistente = await _login(app_vacio, "nadie@otra.co", "loquesea")

    assert clave_mala.status_code == 401
    assert _401(clave_mala) == _401(inexistente)        # status + body idénticos: no filtra cuál falló


async def test_identidad_inactiva_da_401():
    app = _app(_FakeDirectorio([_ident(activo=False)]), _FakeLockout())
    r = await _login(app, "ana@clinica.co", "clave-correcta")
    assert r.status_code == 401


async def test_identidad_sin_password_da_401():
    # password_hash NULL (set-password pendiente): no autentica, mismo 401.
    app = _app(_FakeDirectorio([_ident(con_password=False)]), _FakeLockout())
    r = await _login(app, "ana@clinica.co", "clave-correcta")
    assert r.status_code == 401


async def test_slug_none_se_trata_como_fallo_de_auth():
    # Credencial correcta pero la empresa no resuelve slug (estado inconsistente) → 401, no 500.
    app = _app(_FakeDirectorio([_ident()], slug=None), _FakeLockout())
    r = await _login(app, "ana@clinica.co", "clave-correcta")
    assert r.status_code == 401


# --- lockout -----------------------------------------------------------------

async def test_lockout_429_tras_n_fallos():
    lockout = _FakeLockout(max_intentos=3)
    app = _app(_FakeDirectorio([_ident()]), lockout)
    # 3 intentos fallidos → 401 cada uno (cuentan el fallo).
    for _ in range(3):
        r = await _login(app, "ana@clinica.co", "incorrecta")
        assert r.status_code == 401
    # El 4º intento (incluso con la clave CORRECTA) ya está bloqueado → 429.
    r = await _login(app, "ana@clinica.co", "clave-correcta")
    assert r.status_code == 429


async def test_ya_bloqueado_responde_429_sin_verificar():
    lockout = _FakeLockout(max_intentos=2)
    lockout.fallos["ana@clinica.co"] = 2                # ya en el tope
    app = _app(_FakeDirectorio([_ident()]), lockout)
    r = await _login(app, "ana@clinica.co", "clave-correcta")
    assert r.status_code == 429


# --- cubo por IP (anti password-spraying) ------------------------------------

async def test_spraying_por_ip_bloquea_429_aunque_rote_emails():
    """Una IP probando contraseñas contra MUCHOS emails: el cubo por email nunca llega a su tope,
    pero el cubo por IP sí — el intento N+1 responde 429 aunque el email sea nuevo."""
    lockout_email = _FakeLockout(max_intentos=5)
    lockout_ip = _FakeLockout(max_intentos=3)
    app = _app(_FakeDirectorio([]), lockout_email, lockout_ip)

    async with _cliente(app) as c:
        for i in range(3):   # 3 fallos desde la misma IP, cada uno con un email DISTINTO
            r = await c.post(
                "/api/v1/auth/login/password",
                json={"email": f"victima{i}@x.co", "password": "adivinada"},
                headers={"X-Forwarded-For": "9.9.9.9"},
            )
            assert r.status_code == 401
        r = await c.post(
            "/api/v1/auth/login/password",
            json={"email": "victima99@x.co", "password": "adivinada"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
    assert r.status_code == 429   # bloqueado por IP, no por email
    assert max(lockout_email.fallos.values()) == 1   # ningún email llegó a su tope


async def test_login_ok_resetea_solo_el_cubo_de_email():
    """El éxito resetea el cubo del email (usuario legítimo que se equivocó) pero NO el de IP:
    un spraying con una credencial válida a mano no puede limpiar su contador."""
    lockout_email = _FakeLockout(max_intentos=5)
    lockout_ip = _FakeLockout(max_intentos=30)
    app = _app(_FakeDirectorio([_ident()]), lockout_email, lockout_ip)

    async with _cliente(app) as c:
        await c.post(
            "/api/v1/auth/login/password",
            json={"email": "ana@clinica.co", "password": "mala"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
        r = await c.post(
            "/api/v1/auth/login/password",
            json={"email": "ana@clinica.co", "password": "clave-correcta"},
            headers={"X-Forwarded-For": "9.9.9.9"},
        )
    assert r.status_code == 200
    assert "ana@clinica.co" in lockout_email.reseteos   # el cubo del email se limpia
    assert lockout_ip.reseteos == []                    # el de IP expira solo por TTL
    assert lockout_ip.fallos.get("ip:9.9.9.9") == 1
