"""Router de cuentas por pagar a proveedor. Pack `pos` (ADR 0008): proveedores dejó de ser núcleo;
sin la capacidad `pos`, todo el router responde 404. RBAC = admin. La subida de foto se gatea a
"Cloudinary configurado": si la empresa no lo tiene, responde 503 sin romper las cuentas por pagar.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from modules.proveedores.cloudinary_client import CloudinaryClient
from modules.proveedores.cloudinary_config import cargar_config_cloudinary
from modules.proveedores.errors import (
    AbonoInvalido,
    FacturaProveedorDuplicada,
    FacturaProveedorInexistente,
)
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import (
    AbonoCrear,
    FacturaProveedorCrear,
    FacturaProveedorLeer,
    ProveedorLeer,
    ResumenCxP,
)
from modules.proveedores.service import ProveedoresService

router = APIRouter(tags=["proveedores"], dependencies=[Depends(require_feature("pos"))])


def _service(session: AsyncSession) -> ProveedoresService:
    return ProveedoresService(SqlProveedoresRepository(session))


async def get_cloudinary_client(request: Request) -> CloudinaryClient | None:
    """Cliente Cloudinary de la empresa, o None si no está configurada (descifra del control DB)."""
    async with control_session() as cs:
        cred = await cargar_config_cloudinary(
            cs, get_settings().secrets_master_key, request.state.tenant.id
        )
    return CloudinaryClient(cred) if cred is not None else None


@router.get("/proveedores", response_model=list[ProveedorLeer])
async def listar_proveedores(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[ProveedorLeer]:
    """Proveedores registrados (id/nombre/nit) — alimenta el select de proveedor del modal de producto."""
    return await _service(session).listar_proveedores()


@router.post(
    "/proveedores/facturas", response_model=FacturaProveedorLeer, status_code=status.HTTP_201_CREATED
)
async def crear_factura(
    payload: FacturaProveedorCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
) -> FacturaProveedorLeer:
    """Registra una deuda a proveedor (pendiente=total, estado='pendiente'). id duplicado → 409."""
    try:
        return await _service(session).crear_factura(payload, usuario_id=user.user_id)
    except FacturaProveedorDuplicada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post(
    "/proveedores/abonos", response_model=FacturaProveedorLeer, status_code=status.HTTP_201_CREATED
)
async def crear_abono(
    payload: AbonoCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> FacturaProveedorLeer:
    """Registra un abono y devuelve la factura con el saldo recalculado. 404 / 422 según el caso."""
    try:
        return await _service(session).registrar_abono(payload)
    except FacturaProveedorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AbonoInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/proveedores/facturas", response_model=list[FacturaProveedorLeer])
async def listar_facturas(
    estado: str | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[FacturaProveedorLeer]:
    """Lista de cuentas por pagar (con su saldo), filtrable por estado."""
    return await _service(session).listar(estado=estado)


@router.get("/proveedores/resumen", response_model=ResumenCxP)
async def resumen(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ResumenCxP:
    """Resumen de cuentas por pagar: total adeudado y nº de facturas pendientes."""
    return await _service(session).resumen()


@router.post("/proveedores/facturas/{factura_id}/foto", response_model=FacturaProveedorLeer)
async def subir_foto(
    factura_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
    cloud: CloudinaryClient | None = Depends(get_cloudinary_client),
) -> FacturaProveedorLeer:
    """Sube la foto/soporte a Cloudinary y guarda su URL. 503 si la empresa no tiene Cloudinary; 404 si
    la factura no existe."""
    if cloud is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Fotos no disponibles: Cloudinary no configurado")
    datos = await file.read()
    url = await cloud.subir(datos, filename=file.filename)
    try:
        return await _service(session).guardar_foto(factura_id, url=url, nombre=file.filename)
    except FacturaProveedorInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
