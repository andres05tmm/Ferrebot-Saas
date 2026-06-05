"""Router de compras fiscales (gateado por la capacidad OPCIONAL `compras_fiscal`). RBAC = admin.

Sin la feature, las rutas responden 404 (como si no existieran, feature-flags.md). Dos planos:
- **DATOS (Slice 6a):** registrar/listar compras fiscales y derivarlas de una compra normal. SIN MATIAS.
- **RADIAN-FE (Slice 6b):** eventos DIAN REALES sobre la factura recibida (acuse/aceptar/reclamar). El
  cliente MATIAS por-empresa se compone en `get_radian_deps` (perezoso) y se inyecta en `RadianService`;
  un fallo de MATIAS responde **502** con el `evento_error` ya persistido (no rompe). CUFE MANUAL.

La lógica vive en los servicios; aquí solo se valida, se compone y se mapea a HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from modules.compras_fiscal.errors import (
    CompraFiscalInexistente,
    CompraInexistente,
    CufeNoImportado,
)
from modules.compras_fiscal.radian_service import RadianMatias, RadianService
from modules.compras_fiscal.repository import SqlComprasFiscalRepository
from modules.compras_fiscal.schemas import (
    AmbienteFiscal,
    CompraFiscalCrear,
    CompraFiscalLeer,
    ImportarCufe,
    ReclamarMotivo,
)
from modules.compras_fiscal.service import ComprasFiscalService
from modules.facturacion.config import cargar_ambiente, cargar_config_matias
from modules.facturacion.matias_client import MatiasClient

router = APIRouter(tags=["compras-fiscal"], dependencies=[Depends(require_feature("compras_fiscal"))])


def _service(session: AsyncSession) -> ComprasFiscalService:
    return ComprasFiscalService(SqlComprasFiscalRepository(session))


@dataclass(frozen=True, slots=True)
class RadianDeps:
    """Dependencias de RADIAN compuestas por-empresa: cliente MATIAS perezoso + ambiente declarado."""

    matias: RadianMatias
    ambiente: str


async def get_radian_deps(request: Request) -> RadianDeps:
    """Compone el cliente MATIAS de la empresa (perezoso, sin red al construir) + su ambiente DIAN.

    Descifra las credenciales del control DB (sesión per-call). Overridable en tests para FAKEAR MATIAS.
    """
    async with control_session() as cs:
        cred, config = await cargar_config_matias(
            cs, get_settings().secrets_master_key, request.state.tenant.id
        )
    return RadianDeps(matias=MatiasClient(cred), ambiente=config.ambiente)


def get_radian_service(
    session: AsyncSession = Depends(get_tenant_db),
    deps: RadianDeps = Depends(get_radian_deps),
) -> RadianService:
    """`RadianService` sobre la sesión del tenant + el cliente MATIAS de la empresa (overridable)."""
    return RadianService(SqlComprasFiscalRepository(session), deps.matias, ambiente=deps.ambiente)


# ---- DATOS (Slice 6a) ------------------------------------------------------
@router.post("/compras-fiscal", response_model=CompraFiscalLeer, status_code=status.HTTP_201_CREATED)
async def crear_compra_fiscal(
    payload: CompraFiscalCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Registra una compra fiscal con su desglose de IVA (alimenta el Libro IVA)."""
    return await _service(session).registrar(payload)


@router.get("/compras-fiscal", response_model=list[CompraFiscalLeer])
async def listar_compras_fiscal(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[CompraFiscalLeer]:
    """Compras fiscales del rango (default mes en curso, hora Colombia)."""
    return await _service(session).listar(desde=desde, hasta=hasta)


@router.get("/compras-fiscal/ambiente", response_model=AmbienteFiscal)
async def ambiente_fiscal(
    request: Request,
    _user: Principal = Depends(require_role("admin")),
) -> AmbienteFiscal:
    """Ambiente DIAN declarado de la empresa ('produccion'|'pruebas') para la confirmación del operador."""
    async with control_session() as cs:
        ambiente = await cargar_ambiente(cs, request.state.tenant.id)
    return AmbienteFiscal(ambiente=ambiente)


@router.post("/compras/{compra_id}/to-fiscal", response_model=CompraFiscalLeer)
async def compra_a_fiscal(
    compra_id: int,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Crea una compra fiscal a partir de una compra normal (idempotente). 201 si la crea, 200 si ya
    existía; 404 si la compra no existe."""
    try:
        fiscal, creada = await _service(session).desde_compra(compra_id)
    except CompraInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    response.status_code = status.HTTP_201_CREATED if creada else status.HTTP_200_OK
    return fiscal


# ---- RADIAN-FE recibidas (Slice 6b) — eventos DIAN REALES ------------------
@router.post("/compras-fiscal/{fiscal_id}/importar", response_model=CompraFiscalLeer)
async def importar_cufe(
    fiscal_id: int,
    payload: ImportarCufe,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Importa el CUFE de la factura recibida y envía el acuse 030. 404 si no existe; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.importar(fiscal_id, payload.cufe.strip())
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal


@router.post("/compras-fiscal/{fiscal_id}/aceptar", response_model=CompraFiscalLeer)
async def aceptar_factura(
    fiscal_id: int,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Acepta la FE recibida: envía 032 + 033 → estado 'aceptada'. 404/409; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.aceptar(fiscal_id)
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CufeNoImportado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal


@router.post("/compras-fiscal/{fiscal_id}/reclamar", response_model=CompraFiscalLeer)
async def reclamar_factura(
    fiscal_id: int,
    payload: ReclamarMotivo,
    response: Response,
    service: RadianService = Depends(get_radian_service),
    _user: Principal = Depends(require_role("admin")),
) -> CompraFiscalLeer:
    """Reclama la FE recibida: envía el evento 031 → estado 'reclamada'. 404/409; 502 si MATIAS falla."""
    try:
        fiscal, ok = await service.reclamar(fiscal_id, payload.motivo)
    except CompraFiscalInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CufeNoImportado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if not ok:
        response.status_code = status.HTTP_502_BAD_GATEWAY
    return fiscal
