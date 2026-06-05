"""Entregable 1 (integración) — el webhook mapea telegram_id contra la base REAL de la empresa
y la sesión queda atada a la empresa resuelta (aislamiento).

Usa bases efímeras (conftest). Prueba que:
  - un update válido resuelve el usuario por `usuarios.telegram_id` de ESA base y arma el Contexto;
  - el mismo telegram_id ruteado a otra empresa (que no tiene ese usuario) cae en NO_AUTORIZADO
    → la sesión está atada a la empresa resuelta, no a una global ni a la de otra empresa.
"""
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.bot.ports import Accion, BotDeps
from apps.bot.repos import SqlUsuariosBotRepo
from apps.bot.webhook import manejar_update
from core.db.engine_cache import engine_cache
from core.db.session import tenant_session
from core.tenancy.context import ResolvedTenant

SECRET = "s3cr3t-webhook"


class _Notif:
    def __init__(self):
        self.enviados = []

    async def responder(self, chat_id, texto):
        self.enviados.append((chat_id, texto))


class _Bundle:
    def __init__(self, notif):
        self.notificador = notif
        self.transcriptor = None
        self.archivos = None


class _RecursosBot:
    """`RecursosBot` falso: un bundle por empresa (aquí el webhook solo usa `notificador`)."""

    def __init__(self, notif):
        self._bundle = _Bundle(notif)

    async def para(self, empresa_id):
        return self._bundle


class _Secretos:
    async def webhook_secret(self, empresa_id):
        return SECRET

    async def bot_token(self, empresa_id):
        return "tok"


class _Caps:
    async def efectivas(self, empresa_id):
        return frozenset({"bot_telegram"})


class _Dedup:
    def __init__(self):
        self.v = set()

    async def marcar_si_nuevo(self, t, u):
        if (t, u) in self.v:
            return False
        self.v.add((t, u))
        return True


class _Resolver:
    def __init__(self, mapa):
        self._m = mapa

    async def por_slug(self, slug):
        return self._m.get(slug)


class _Spy:
    def __init__(self):
        self.ctx = None
        self.session = None

    async def __call__(self, update, ctx, session, notif):
        self.ctx = ctx
        self.session = session


def _abrir_sesion(tenant):
    @asynccontextmanager
    async def _cm():
        async for s in tenant_session(tenant):
            yield s

    return _cm()


def _resolved(tdb, *, slug) -> ResolvedTenant:
    # id único por base efímera para no chocar en el engine_cache global.
    tid = abs(hash(tdb.name)) % 2_000_000 + 1000
    return ResolvedTenant(
        id=tid, slug=slug, estado="activa", db_name=tdb.name, connection_url=tdb.url
    )


async def _seed_usuario(engine, *, telegram_id: int, rol: str = "vendedor") -> int:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(
                text(
                    "INSERT INTO usuarios (nombre, rol, telegram_id) "
                    "VALUES ('Vendedor', :r, :t) RETURNING id"
                ),
                {"r": rol, "t": telegram_id},
            )
        ).scalar_one()
        await s.commit()
    return uid


def _payload(update_id: int, telegram_id: int, texto: str = "2 martillo") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1, "from": {"id": telegram_id},
            "chat": {"id": telegram_id}, "text": texto,
        },
    }


def _deps(resolver, spy) -> BotDeps:
    return BotDeps(
        resolver=resolver, secretos=_Secretos(), capacidades=_Caps(), dedup=_Dedup(),
        abrir_sesion=_abrir_sesion, usuarios=lambda s: SqlUsuariosBotRepo(s),
        recursos=_RecursosBot(_Notif()), procesar=spy,
    )


async def test_mapea_usuario_real_por_telegram_id(tenant):
    try:
        uid = await _seed_usuario(tenant.engine, telegram_id=999)
        rt = _resolved(tenant, slug="puntorojo")
        spy = _Spy()
        deps = _deps(_Resolver({"puntorojo": rt}), spy)

        res = await manejar_update("puntorojo", SECRET, _payload(1, 999), deps)

        assert res.accion is Accion.PROCESADO
        assert res.ctx is not None
        assert res.ctx.usuario_id == uid
        assert res.ctx.rol == "vendedor"
        assert res.ctx.tenant_id == rt.id
        assert spy.ctx is res.ctx
    finally:
        await engine_cache.dispose_all()


async def test_sesion_atada_a_empresa_resuelta(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    try:
        await _seed_usuario(empresa_a.engine, telegram_id=999)   # usuario SOLO en A
        rt_b = _resolved(empresa_b, slug="otra")
        spy = _Spy()
        deps = _deps(_Resolver({"otra": rt_b}), spy)

        # mismo telegram_id, pero el update se rutea a B: su base no tiene ese usuario
        res = await manejar_update("otra", SECRET, _payload(1, 999), deps)

        assert res.accion is Accion.NO_AUTORIZADO
        assert spy.ctx is None
    finally:
        await engine_cache.dispose_all()
