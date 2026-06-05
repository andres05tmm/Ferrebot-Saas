"""Router de clientes (B2, api-contract.md): listar, crear (con dedup), obtener.

Núcleo (siempre activo, feature-flags.md): sin require_feature. Lecturas y alta son de rol
`vendedor` o superior. La lógica vive en `ClientesService` (dedup por documento); aquí solo se
valida, se mapea a HTTP y se serializa. El servicio se inyecta por dependencia (overridable en test).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from modules.clientes.repository import SqlClientesRepository
from modules.clientes.schemas import ClienteCrear, ClienteLeer
from modules.clientes.service import ClientesService
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.matias_client import MatiasClient

router = APIRouter(tags=["clientes"])


def get_clientes_service(session: AsyncSession = Depends(get_tenant_db)) -> ClientesService:
    """Arma el `ClientesService` sobre la sesión del tenant (los tests lo overridean con un fake)."""
    return ClientesService(SqlClientesRepository(session))


async def get_matias_client(request: Request) -> AsyncIterator[MatiasClient]:
    """MatiasClient por empresa para los catálogos fiscales (ciudades/países); se cierra al terminar.

    Mismo patrón que `get_facturacion_service`: credenciales descifradas con `cargar_config_matias`
    sobre una sesión de control per-call. Inyectable (los tests lo overridean con un fake, sin red).
    """
    async with control_session() as cs:
        cred, _config = await cargar_config_matias(
            cs, get_settings().secrets_master_key, request.state.tenant.id
        )
    client = MatiasClient(cred)
    try:
        yield client
    finally:
        await client.aclose()


@router.get("/clientes", response_model=list[ClienteLeer])
async def listar_clientes(
    q: str | None = Query(default=None, description="Filtra por nombre o documento (ILIKE)"),
    service: ClientesService = Depends(get_clientes_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ClienteLeer]:
    clientes = await service.listar(q)
    return [ClienteLeer.model_validate(c) for c in clientes]


@router.post("/clientes", response_model=ClienteLeer, status_code=status.HTTP_201_CREATED)
async def crear_cliente(
    payload: ClienteCrear,
    response: Response,
    service: ClientesService = Depends(get_clientes_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ClienteLeer:
    """Crea el cliente; si ya existía por documento (dedup), responde 200 con el existente."""
    resultado = await service.crear(payload)
    if not resultado.creado:
        response.status_code = status.HTTP_200_OK
    return ClienteLeer.model_validate(resultado.cliente)


# Catálogos fiscales (MATIAS por empresa). Gateados por la feature; SOLO el dashboard fiscal los pide.
# Declarados ANTES de /clientes/{cliente_id} para que "ciudades"/"paises" no entren como path param.
@router.get(
    "/clientes/ciudades",
    dependencies=[Depends(require_feature("facturacion_electronica"))],
)
async def listar_ciudades(
    pais_id: int = Query(default=45),
    q: str = Query(default=""),
    matias: MatiasClient = Depends(get_matias_client),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[dict]:
    """Ciudades (DANE + nombre) del país para el selector fiscal del form de cliente."""
    return await matias.listar_ciudades(pais_id=pais_id, q=q)


@router.get(
    "/clientes/paises",
    dependencies=[Depends(require_feature("facturacion_electronica"))],
)
async def listar_paises(
    matias: MatiasClient = Depends(get_matias_client),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[dict]:
    """Países de MATIAS para el selector fiscal del form de cliente."""
    return await matias.listar_paises()


@router.get("/clientes/{cliente_id}", response_model=ClienteLeer)
async def obtener_cliente(
    cliente_id: int,
    service: ClientesService = Depends(get_clientes_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ClienteLeer:
    cliente = await service.obtener(cliente_id)
    if cliente is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Cliente {cliente_id} no existe")
    return ClienteLeer.model_validate(cliente)
