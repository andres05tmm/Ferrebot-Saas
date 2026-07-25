"""Router del perfil del usuario autenticado (`/perfil*`). Núcleo: sin require_feature.

Todo opera sobre el PROPIO usuario del token — el id nunca viene por parámetro: un vendedor jamás
ve el perfil ni las acciones de otro. El email sale del directorio de identidades (control DB);
nombre, foto y color viven en `usuarios` (tenant). La foto va al Cloudinary de la empresa (reusa
el cliente por-tenant de proveedores); sin Cloudinary configurado, subir foto responde 503 sin
romper el resto del perfil.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.config.timezone import rango_dia_co, today_co
from core.db.session import control_session, get_tenant_db
from core.tenancy.identidades_repo import email_de_usuario
from modules.perfil.repository import SqlPerfilRepository
from modules.proveedores.cloudinary_client import CloudinaryClient
from modules.proveedores.router import get_cloudinary_client

router = APIRouter(tags=["perfil"])


class PerfilLeer(BaseModel):
    id: int
    nombre: str
    rol: str
    email: str | None
    avatar_url: str | None
    color: str | None
    creado_en: datetime | None


class PerfilEditar(BaseModel):
    """Campos personalizables del propio perfil. Solo se actualiza lo provisto."""

    nombre: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class AccionLeer(BaseModel):
    tipo: str
    ref_id: int
    fecha: datetime
    monto: Decimal | None
    detalle: str
    estado: str


class ResumenLeer(BaseModel):
    ventas: int
    total_vendido: Decimal
    gastos: int
    abonos: int
    compras: int


class AccionesLeer(BaseModel):
    resumen: ResumenLeer
    acciones: list[AccionLeer]


async def _perfil_o_404(repo: SqlPerfilRepository, usuario_id: int):
    fila = await repo.obtener(usuario_id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    return fila


async def _leer(session: AsyncSession, user: Principal, empresa_id: int) -> PerfilLeer:
    fila = await _perfil_o_404(SqlPerfilRepository(session), user.user_id)
    # El email es el de la identidad CON LA QUE se entró (claim del login). Un usuario puede tener
    # varias identidades; el directorio no sabe cuál se usó — solo el token. Fallback al directorio
    # para tokens sin claim (viejos, Telegram, dev_token).
    email = user.email
    if email is None:
        async with control_session() as cs:
            email = await email_de_usuario(cs, empresa_id, user.user_id)
    return PerfilLeer(
        id=fila.id, nombre=fila.nombre, rol=fila.rol, email=email,
        avatar_url=fila.avatar_url, color=fila.color, creado_en=fila.creado_en,
    )


@router.get("/perfil", response_model=PerfilLeer)
async def obtener_perfil(
    request: Request,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> PerfilLeer:
    """Perfil del usuario del token: identidad + personalización."""
    return await _leer(session, user, request.state.tenant.id)


@router.patch("/perfil", response_model=PerfilLeer)
async def editar_perfil(
    payload: PerfilEditar,
    request: Request,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> PerfilLeer:
    """Actualiza nombre y/o color del propio perfil."""
    repo = SqlPerfilRepository(session)
    await _perfil_o_404(repo, user.user_id)
    nombre = payload.nombre.strip() if payload.nombre is not None else None
    if nombre == "":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "El nombre no puede estar vacío")
    await repo.actualizar(user.user_id, nombre=nombre, color=payload.color)
    return await _leer(session, user, request.state.tenant.id)


@router.post("/perfil/foto", response_model=PerfilLeer)
async def subir_foto(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    cloud: CloudinaryClient | None = Depends(get_cloudinary_client),
) -> PerfilLeer:
    """Sube la foto de perfil a Cloudinary y guarda su URL. 503 si la empresa no tiene Cloudinary."""
    if cloud is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Fotos no disponibles: Cloudinary no configurado"
        )
    repo = SqlPerfilRepository(session)
    await _perfil_o_404(repo, user.user_id)
    datos = await file.read()
    url = await cloud.subir(datos, filename=file.filename)
    await repo.actualizar(user.user_id, avatar_url=url)
    return await _leer(session, user, request.state.tenant.id)


@router.get("/perfil/acciones", response_model=AccionesLeer)
async def listar_acciones(
    dias: int = Query(default=7, ge=1, le=90),
    limite: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> AccionesLeer:
    """Historial de acciones del PROPIO usuario en los últimos `dias` (hora Colombia): ventas,
    gastos, abonos de fiados, compras a proveedor y aperturas/cierres de caja, más reciente primero.
    `resumen` agrega el mismo rango completo (independiente de la paginación)."""
    desde, _ = rango_dia_co(today_co() - timedelta(days=dias - 1), today_co())
    repo = SqlPerfilRepository(session)
    resumen = await repo.resumen(user.user_id, desde=desde)
    acciones = await repo.acciones(user.user_id, desde=desde, limite=limite, offset=offset)
    return AccionesLeer(
        resumen=ResumenLeer(
            ventas=resumen.ventas, total_vendido=resumen.total_vendido,
            gastos=resumen.gastos, abonos=resumen.abonos, compras=resumen.compras,
        ),
        acciones=[
            AccionLeer(
                tipo=a.tipo, ref_id=a.ref_id, fecha=a.fecha, monto=a.monto,
                detalle=a.detalle, estado=a.estado,
            )
            for a in acciones
        ],
    )
