"""SMOKE del composition root del bot (`apps.bot.wiring.construir_deps`).

Verifica el CABLEADO sin red:
  - `construir_deps` (con todos los seams inyectados) devuelve un `BotDeps` con todos los puertos;
  - un update de texto de un telegram_id sin usuario recorre el webhook real hasta "no autorizado"
    respondiendo por `bundle.notificador` — sin Telegram, sin LLM, sin Redis real (el camino corta
    antes del dispatcher). El control DB y el tenant DB son efímeros; el resto se inyecta con fakes.

RED: `construir_deps` lanza NotImplementedError → ambas pruebas fallan hasta GREEN.
"""
import uuid
from contextlib import asynccontextmanager

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from apps.bot.telegram import TelegramArchivos, TelegramNotificador
from apps.bot.webhook import _MSG_NO_AUTORIZADO, crear_app_bot
from apps.bot.wiring import construir_deps
from core.config import get_settings
from core.crypto import encrypt, encrypt_split
from core.db.urls import tenant_url, to_async
from core.tenancy.cache import control_cache
from core.voz.transcriptor import WhisperTranscriptor
from tests.conftest import create_database, drop_database

SECRET = "s3cr3t-webhook"
_CHAT = 555
_TELEGRAM_ID = 555


# --------------------------------- fakes ----------------------------------

class GrabadorNotificador:
    """Notificador grabador (cero red): registra (chat_id, texto)."""

    def __init__(self):
        self.enviados: list[tuple[int, str]] = []

    async def responder(self, chat_id: int, texto: str) -> None:
        self.enviados.append((chat_id, texto))


class _FakeTranscriptor:
    async def transcribir(self, audio, *, prompt=None):
        raise AssertionError("no debería transcribir en el camino no-autorizado")


class _FakeArchivos:
    async def descargar(self, file_id):
        raise AssertionError("no debería descargar en el camino no-autorizado")


class _Bundle:
    def __init__(self, notificador):
        self.notificador = notificador
        self.transcriptor = _FakeTranscriptor()
        self.archivos = _FakeArchivos()


class FakeRecursosBot:
    def __init__(self, notificador):
        self._bundle = _Bundle(notificador)

    async def para(self, empresa_id):
        return self._bundle


class FakeDedup:
    async def marcar_si_nuevo(self, tenant_id, update_id):
        return True                       # siempre nuevo (no es la ruta bajo prueba)


class FakeConfirm:
    async def obtener(self, tenant_id, chat_id):
        return None

    async def guardar(self, *a, **k):
        pass

    async def borrar(self, tenant_id, chat_id):
        pass


def _payload_texto(update_id=100, chat_id=_CHAT, telegram_id=_TELEGRAM_ID, texto="2 martillo"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1, "from": {"id": telegram_id},
            "chat": {"id": chat_id}, "text": texto,
        },
    }


# ---------------------- construir_deps cablea los puertos ----------------------

async def test_construir_deps_cablea_todos_los_puertos():
    @asynccontextmanager
    async def _abrir_control():
        raise AssertionError("no debería abrirse en el mero cableado")
        yield  # pragma: no cover

    def _abrir_tenant(_t):
        @asynccontextmanager
        async def _cm():
            raise AssertionError("no debería abrirse en el mero cableado")
            yield  # pragma: no cover

        return _cm()

    deps = construir_deps(
        settings=get_settings(),
        abrir_control=_abrir_control,
        abrir_tenant=_abrir_tenant,
        dedup=FakeDedup(),
        confirm=FakeConfirm(),
        recursos=FakeRecursosBot(GrabadorNotificador()),
    )

    for campo in (
        "resolver", "secretos", "capacidades", "dedup",
        "abrir_sesion", "usuarios", "recursos", "procesar",
    ):
        assert getattr(deps, campo) is not None, f"BotDeps.{campo} sin cablear"


# ----------------- update no autorizado recorre el webhook real ----------------

async def test_update_no_autorizado_recorre_webhook_sin_red(tenant, monkeypatch):
    control_cache.invalidate("pr")          # hardening: sin fuga del cache entre archivos del proceso
    name = f"test_control_wiring_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    control_engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        master = get_settings().secrets_master_key
        ws_ct, ws_n = encrypt_split(SECRET, master)
        # connection_url cifrada (no se usa: abrir_tenant se inyecta), pero resolve_tenant la descifra.
        conn_cifrada = encrypt(f"postgresql://u:p@h/{tenant.name}", master)

        async with AsyncSession(control_engine) as s:
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado) "
                        "VALUES ('Punto Rojo', '900', 'pr', 'activa') RETURNING id"
                    )
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO tenant_databases (empresa_id, db_name, host, connection_url_cifrada) "
                    "VALUES (:e, :db, 'h', :u)"
                ),
                {"e": eid, "db": tenant.name, "u": conn_cifrada},
            )
            await s.execute(
                text(
                    "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
                    "VALUES (:e, 'telegram_webhook_secret', :c, :n)"
                ),
                {"e": eid, "c": ws_ct, "n": ws_n},
            )
            await s.commit()

        @asynccontextmanager
        async def abrir_control():
            async with AsyncSession(control_engine, expire_on_commit=False) as cs:
                yield cs

        def abrir_tenant(_t):
            @asynccontextmanager
            async def _cm():
                async with AsyncSession(tenant.engine, expire_on_commit=False) as ts:
                    yield ts                # tenant efímero SIN usuarios → no autorizado

            return _cm()

        notif = GrabadorNotificador()
        deps = construir_deps(
            settings=get_settings(),
            abrir_control=abrir_control,
            abrir_tenant=abrir_tenant,
            dedup=FakeDedup(),
            confirm=FakeConfirm(),
            recursos=FakeRecursosBot(notif),
        )
        app = crear_app_bot(deps)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/tg/pr",
                json=_payload_texto(),
                headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
            )

        assert r.status_code == 200
        assert r.json()["accion"] == "no_autorizado"
        assert notif.enviados == [(_CHAT, _MSG_NO_AUTORIZADO)]   # respondió por bundle.notificador
    finally:
        await control_engine.dispose()
        get_settings.cache_clear()
        drop_database(name)


async def test_cargar_por_defecto_resuelve_credenciales_sin_red(monkeypatch):
    # El RecursosBot REAL (default, sin inyectar) descifra bot_token + openai_api_key del control DB
    # en UNA sesión y arma los adaptadores perezosos (cero red al construirlos).
    name = f"test_control_cargar_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    control_engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        master = get_settings().secrets_master_key
        tok_ct, tok_n = encrypt_split("123456:BOT-TOKEN", master)
        key_ct, key_n = encrypt_split("sk-openai-real", master)

        async with AsyncSession(control_engine) as s:
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado) "
                        "VALUES ('Punto Rojo', '900', 'pr', 'activa') RETURNING id"
                    )
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) VALUES "
                    "(:e, 'telegram_token', :c1, :n1), (:e, 'openai_api_key', :c2, :n2)"
                ),
                {"e": eid, "c1": tok_ct, "n1": tok_n, "c2": key_ct, "n2": key_n},
            )
            await s.commit()

        @asynccontextmanager
        async def abrir_control():
            async with AsyncSession(control_engine, expire_on_commit=False) as cs:
                yield cs

        deps = construir_deps(settings=get_settings(), abrir_control=abrir_control)
        bundle = await deps.recursos.para(eid)        # RecursosBot real con _cargar (1 sesión)

        assert isinstance(bundle.notificador, TelegramNotificador)
        assert isinstance(bundle.transcriptor, WhisperTranscriptor)
        assert isinstance(bundle.archivos, TelegramArchivos)
        # credenciales descifradas del control DB (assert directo del token/key)
        assert bundle.notificador._bot_token == "123456:BOT-TOKEN"
        assert bundle.archivos._bot_token == "123456:BOT-TOKEN"
        assert bundle.transcriptor._api_key == "sk-openai-real"
    finally:
        await control_engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
