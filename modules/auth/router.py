"""Router de autenticación del dashboard: login por Telegram Login Widget → JWT.

- `POST /auth/login`: verifica el payload firmado del widget con el bot-token de ESA empresa
  (secreto cifrado en `secretos_empresa`, control DB), mapea `telegram_id` → usuario ACTIVO
  (sesión del tenant) y emite el JWT (claim `tenant` = slug de la empresa).
- `GET /auth/me`: devuelve la identidad del `Principal` del token actual.

Sin feature-gate (es la puerta de entrada). El tenant ya lo resolvió `TenantMiddleware`
(`request.state.tenant`): de ahí salen el `empresa_id` (para leer el secreto) y el `slug` (claim).

Reusa, sin reimplementar, las piezas del bot (`apps.bot`): `ControlSecretosBot` para el bot-token
por empresa y `SqlUsuariosBotRepo` para el mapeo `telegram_id` → usuario sobre la sesión del tenant.
Ambos se inyectan por dependencias (`SecretosBot`, `UsuariosBotRepo`) para poder testear sin red.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.bot.ports import SecretosBot, UsuariosBotRepo
from apps.bot.repos import ControlSecretosBot, SqlUsuariosBotRepo
from core.auth import Principal, create_access_token, get_current_user
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from core.logging import get_logger
from core.tenancy.context import ResolvedTenant
from modules.auth.telegram import verificar_widget

router = APIRouter(tags=["auth"])
log = get_logger("auth")


class TelegramLogin(BaseModel):
    """Payload firmado que el Telegram Login Widget entrega al frontend."""

    id: int                              # telegram_id del usuario → usuarios.telegram_id
    auth_date: int
    hash: str
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None

    def datos_widget(self) -> dict[str, object]:
        """Campos presentes (sin None) para reconstruir el data_check_string del widget."""
        return {clave: valor for clave, valor in self.model_dump().items() if valor is not None}


class UsuarioOut(BaseModel):
    """Identidad del usuario autenticado (la misma forma para /login y /me)."""

    id: int
    rol: str
    tenant: str


class LoginOut(BaseModel):
    """Respuesta de POST /auth/login: el JWT y el usuario que entró."""

    token: str
    usuario: UsuarioOut


def get_tenant(request: Request) -> ResolvedTenant:
    """Empresa resuelta por TenantMiddleware (empresa_id para el secreto, slug para el JWT)."""
    return request.state.tenant


class _SecretosControl:
    """Adaptador `SecretosBot`: abre una sesión de control FRESCA por llamada y delega en
    `ControlSecretosBot` (reusa el descifrado de `secretos_empresa` del bot)."""

    def __init__(self, master: str) -> None:
        self._master = master

    async def webhook_secret(self, empresa_id: int) -> str | None:
        async with control_session() as cs:
            return await ControlSecretosBot(cs, self._master).webhook_secret(empresa_id)

    async def bot_token(self, empresa_id: int) -> str | None:
        async with control_session() as cs:
            return await ControlSecretosBot(cs, self._master).bot_token(empresa_id)


async def get_secretos() -> SecretosBot:
    """Lector de secretos por empresa (bot-token cifrado en control DB). Inyectable en tests."""
    return _SecretosControl(get_settings().secrets_master_key)


def get_usuarios(session: AsyncSession = Depends(get_tenant_db)) -> UsuariosBotRepo:
    """Mapeo telegram_id→usuario sobre la sesión del tenant (reusa `SqlUsuariosBotRepo` del bot)."""
    return SqlUsuariosBotRepo(session)


@router.post("/auth/login", response_model=LoginOut)
async def login(
    payload: TelegramLogin,
    tenant: ResolvedTenant = Depends(get_tenant),
    secretos: SecretosBot = Depends(get_secretos),
    usuarios: UsuariosBotRepo = Depends(get_usuarios),
) -> LoginOut:
    """Verifica el widget con el bot-token de la empresa, mapea telegram_id→usuario activo y emite el JWT."""
    bot_token = await secretos.bot_token(tenant.id)
    if not bot_token or not verificar_widget(payload.datos_widget(), bot_token):
        log.warning("login_widget_invalido", tenant=tenant.slug, telegram_id=payload.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma de Telegram inválida")
    usuario = await usuarios.por_telegram_id(payload.id)
    if usuario is None or not usuario.activo:
        log.info("login_usuario_no_autorizado", tenant=tenant.slug, telegram_id=payload.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario no autorizado en esta empresa")
    token = create_access_token(user_id=usuario.id, tenant=tenant.slug, rol=usuario.rol)
    log.info("login_exitoso", tenant=tenant.slug, usuario_id=usuario.id, rol=usuario.rol)
    return LoginOut(
        token=token, usuario=UsuarioOut(id=usuario.id, rol=usuario.rol, tenant=tenant.slug)
    )


@router.get("/auth/me", response_model=UsuarioOut)
async def me(user: Principal = Depends(get_current_user)) -> UsuarioOut:
    """Identidad del usuario autenticado (Principal del token actual)."""
    return UsuarioOut(id=user.user_id, rol=user.rol, tenant=user.tenant)
