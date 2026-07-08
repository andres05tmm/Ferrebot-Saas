"""Router de la cartera de alquiler (backend de la sección "Cartera de alquiler" del dashboard).

Gateado por `require_feature("cartera_alquiler")`: sin el flag, todo el router responde 404 (como si no
existiera, patrón pagar). RBAC: TODO es rol `admin` —el cupo de crédito es dato sensible del negocio,
igual que cobranza/pagar—. Los ABONOS NO viven aquí: van por el router de fiados existente
(`POST /fiados/{fiado_id}/abono`), referenciando la obra desde la UI. El CONSUMO (crítico) tampoco es
endpoint HTTP: lo dispara Fase 3 al registrar horas (seam en `MaquinariaService`). La lógica vive en
`CarteraAlquilerService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.cartera.errors import CupoInexistente
from modules.cartera.schemas import (
    CarteraConfigActualizar,
    CarteraConfigLeer,
    ColitaLeer,
    CupoActualizar,
    CupoCrear,
    CupoLeer,
    ObraCarteraLeer,
)
from modules.cartera.service import CarteraAlquilerService, construir_cartera_service

router = APIRouter(
    prefix="/cartera-alquiler", tags=["cartera-alquiler"],
    dependencies=[Depends(require_feature("cartera_alquiler"))],
)


def get_cartera_service(session: AsyncSession = Depends(get_tenant_db)) -> CarteraAlquilerService:
    """Arma el `CarteraAlquilerService` (con su `FiadosService`) sobre la sesión del tenant."""
    return construir_cartera_service(session)


@router.get("/cupos", response_model=list[CupoLeer])
async def listar_cupos(
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[CupoLeer]:
    """Cupos activos + `consumido`/`disponible`/semáforo por cliente (+ chip colita)."""
    return await service.listar_cupos()


@router.post("/cupos", response_model=CupoLeer, status_code=status.HTTP_201_CREATED)
async def crear_cupo(
    payload: CupoCrear,
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> CupoLeer:
    """Crea un cupo (desactiva el activo previo del cliente). Devuelve la fila con su semáforo en vivo."""
    cupo = await service.crear_cupo(payload)
    return await service.cupo_leer(cupo.id)


@router.put("/cupos/{cupo_id}", response_model=CupoLeer)
async def actualizar_cupo(
    cupo_id: int,
    payload: CupoActualizar,
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> CupoLeer:
    """Edita cupo/vigencia/activo/notas. 404 si no existe."""
    try:
        cupo = await service.actualizar_cupo(cupo_id, payload)
    except CupoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return await service.cupo_leer(cupo.id)


@router.get("/obras/{obra_id}", response_model=ObraCarteraLeer)
async def cartera_de_obra(
    obra_id: int,
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> ObraCarteraLeer:
    """Detalle de cartera de la obra: saldo pendiente + cargos (vista de liquidación)."""
    return await service.cartera_de_obra(obra_id)


@router.get("/colitas", response_model=list[ColitaLeer])
async def listar_colitas(
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[ColitaLeer]:
    """Colitas detectadas (obra cerrada, saldo estancado sin abono > N días) para el semáforo."""
    return await service.listar_colitas()


@router.get("/config", response_model=CarteraConfigLeer)
async def obtener_config(
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> CarteraConfigLeer:
    return CarteraConfigLeer.model_validate(await service.obtener_config())


@router.put("/config", response_model=CarteraConfigLeer)
async def actualizar_config(
    payload: CarteraConfigActualizar,
    service: CarteraAlquilerService = Depends(get_cartera_service),
    _user: Principal = Depends(require_role("admin")),
) -> CarteraConfigLeer:
    config = await service.guardar_config(payload.model_dump())
    return CarteraConfigLeer.model_validate(config)
