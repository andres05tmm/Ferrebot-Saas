"""Router de clientes (B2, api-contract.md): listar, crear (con dedup), obtener.

Núcleo (siempre activo, feature-flags.md): sin require_feature. Lecturas y alta son de rol
`vendedor` o superior. La lógica vive en `ClientesService` (dedup por documento); aquí solo se
valida, se mapea a HTTP y se serializa. El servicio se inyecta por dependencia (overridable en test).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.db.session import get_tenant_db
from modules.clientes.repository import SqlClientesRepository
from modules.clientes.schemas import ClienteCrear, ClienteLeer
from modules.clientes.service import ClientesService

router = APIRouter(tags=["clientes"])


def get_clientes_service(session: AsyncSession = Depends(get_tenant_db)) -> ClientesService:
    """Arma el `ClientesService` sobre la sesión del tenant (los tests lo overridean con un fake)."""
    return ClientesService(SqlClientesRepository(session))


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
