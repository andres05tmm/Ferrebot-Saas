"""Router del motor contable (`/contabilidad/*`). Gateado por `contabilidad_ledger` (404 sin el flag).

Superficie MÍNIMA y de solo-consulta salvo dos acciones de operación (sembrar PUC, backfill), todo de
**admin**: la contabilidad es información sensible del negocio. La lógica vive en los servicios del
módulo; aquí solo se resuelve la sesión del tenant, se valida el rol y se serializa.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.contabilidad.estados import EstadosService
from modules.contabilidad.fuente_repository import FuenteContableRepository
from modules.contabilidad.ledger import LedgerService
from modules.contabilidad.proyector import Proyector
from modules.contabilidad.repository import SqlContabilidadRepository
from modules.contabilidad.schemas import (
    AsientoLeer,
    BalanceComprobacion,
    BalanceGeneral,
    EstadoResultados,
    FlujoEfectivo,
    LineaAsientoLeer,
)

router = APIRouter(
    prefix="/contabilidad", tags=["contabilidad"],
    dependencies=[Depends(require_feature("contabilidad_ledger"))],
)


def _repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlContabilidadRepository:
    return SqlContabilidadRepository(session)


def _estados(repo: SqlContabilidadRepository = Depends(_repo)) -> EstadosService:
    return EstadosService(repo)


@router.post("/puc/sembrar")
async def sembrar_puc(
    repo: SqlContabilidadRepository = Depends(_repo),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, int]:
    """Siembra el PUC del tenant (idempotente). Prerrequisito para proyectar."""
    await repo.asegurar_puc()
    return {"cuentas": len(await repo.cuentas_map())}


@router.post("/backfill")
async def backfill(
    desde: datetime = Query(..., description="Proyecta los eventos con fecha ≥ esta (ISO, TZ Colombia)"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, dict[str, int]]:
    """Proyecta los eventos operativos desde una fecha (solo hacia adelante). Idempotente."""
    repo = SqlContabilidadRepository(session)
    await repo.asegurar_puc()
    proj = Proyector(LedgerService(repo), FuenteContableRepository(session))
    resumen = await proj.backfill(desde)
    return {"creados": resumen.creados, "replay": resumen.replay}


@router.get("/asientos", response_model=list[AsientoLeer])
async def listar_asientos(
    origen_tipo: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    repo: SqlContabilidadRepository = Depends(_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[AsientoLeer]:
    """Últimos asientos (filtrables por tipo de origen), con sus líneas y el código de cuenta."""
    cuentas = {c.id: c for c in (await repo.cuentas_map()).values()}
    asientos = await repo.listar_asientos(limit=limit, origen_tipo=origen_tipo)
    salida: list[AsientoLeer] = []
    for a in asientos:
        salida.append(
            AsientoLeer(
                id=a.id, fecha=a.fecha, estado=a.estado, origen_tipo=a.origen_tipo,
                origen_id=a.origen_id, descripcion=a.descripcion, reverso_de=a.reverso_de,
                lineas=[
                    LineaAsientoLeer(
                        cuenta_codigo=cuentas[ln.cuenta_id].codigo,
                        cuenta_nombre=cuentas[ln.cuenta_id].nombre,
                        direction=ln.direction, amount=ln.amount, descripcion=ln.descripcion,
                    )
                    for ln in a.lineas
                ],
            )
        )
    return salida


@router.get("/balance-comprobacion", response_model=BalanceComprobacion)
async def balance_comprobacion(
    inicio: datetime | None = Query(default=None),
    fin: datetime | None = Query(default=None),
    estados: EstadosService = Depends(_estados),
    _user: Principal = Depends(require_role("admin")),
) -> BalanceComprobacion:
    return await estados.balance_comprobacion(inicio=inicio, fin=fin)


@router.get("/estado-resultados", response_model=EstadoResultados)
async def estado_resultados(
    inicio: datetime | None = Query(default=None),
    fin: datetime | None = Query(default=None),
    estados: EstadosService = Depends(_estados),
    _user: Principal = Depends(require_role("admin")),
) -> EstadoResultados:
    return await estados.estado_resultados(inicio=inicio, fin=fin)


@router.get("/balance-general", response_model=BalanceGeneral)
async def balance_general(
    fin: datetime | None = Query(default=None),
    estados: EstadosService = Depends(_estados),
    _user: Principal = Depends(require_role("admin")),
) -> BalanceGeneral:
    return await estados.balance_general(fin=fin)


@router.get("/flujo-efectivo", response_model=FlujoEfectivo)
async def flujo_efectivo(
    inicio: datetime | None = Query(default=None),
    fin: datetime | None = Query(default=None),
    estados: EstadosService = Depends(_estados),
    _user: Principal = Depends(require_role("admin")),
) -> FlujoEfectivo:
    return await estados.flujo_efectivo(inicio=inicio, fin=fin)
