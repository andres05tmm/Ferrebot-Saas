"""Router REST del pack reservas (dashboard/recepción). Gateado por la capacidad `pack_reservas`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: staff (vendedor+) consulta la
disponibilidad y crea reservas (es el operador de recepción). La lógica —disponibilidad, anti-doble
reserva por lock e idempotencia— vive en `ReservasService` (que reusa el motor de agenda); aquí solo se
valida, se compone y se mapea a HTTP. Sin SQL.

Una reserva ES una cita sobre un recurso `habitacion`: gestionarlas/cancelarlas se hace con las
herramientas de agenda de siempre (`/agenda/citas/...`). Este router solo expone el alta específica de
reservas por noches y la consulta de habitaciones libres.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.agenda.errors import CupoNoDisponible, RecursoInexistente
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import CitaLeer
from modules.reservas.schemas import HabitacionLibreLeer, ReservaCrear, ReservaLeer
from modules.reservas.service import NochesInvalidas, ReservasService

router = APIRouter(
    prefix="/reservas", tags=["reservas"],
    dependencies=[Depends(require_feature("pack_reservas"))],
)


def get_reservas_service(session: AsyncSession = Depends(get_tenant_db)) -> ReservasService:
    """Arma el `ReservasService` sobre la sesión del tenant (los tests lo overridean)."""
    return ReservasService(SqlAgendaRepository(session))


@router.get("/habitaciones", response_model=list[HabitacionLibreLeer])
async def habitaciones_libres(
    checkin: date = Query(...),
    noches: int = Query(ge=1, le=30),
    service: ReservasService = Depends(get_reservas_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[HabitacionLibreLeer]:
    """Habitaciones sin ocupación en [check-in, check-out) con su precio/noche y total del rango."""
    try:
        libres = await service.habitaciones_libres(checkin, noches)
    except NochesInvalidas as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Noches inválidas: {exc}") from exc
    return [
        HabitacionLibreLeer(
            recurso_id=h.recurso_id, nombre=h.nombre,
            precio_noche=h.precio_noche, total=h.total,
        )
        for h in libres
    ]


@router.post("", response_model=ReservaLeer, status_code=status.HTTP_201_CREATED)
async def crear_reserva(
    payload: ReservaCrear,
    response: Response,
    service: ReservasService = Depends(get_reservas_service),
    _user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ReservaLeer:
    """Reserva la habitación N noches (idempotente por Idempotency-Key o campo del payload).

    Replay (misma key) → 200 con la MISMA cita. 404 si la habitación no existe; 409 si el cupo ya no
    está disponible; 422 si las noches están fuera de rango."""
    key = payload.idempotency_key or idempotency_key
    try:
        res = await service.reservar(
            recurso_id=payload.recurso_id, checkin=payload.checkin, noches=payload.noches,
            cliente_nombre=payload.cliente_nombre, cliente_telefono=payload.cliente_telefono,
            idempotency_key=key, origen="dashboard",
        )
    except NochesInvalidas as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Noches inválidas: {exc}") from exc
    except RecursoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CupoNoDisponible as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return ReservaLeer(
        cita=CitaLeer.model_validate(res.cita), replay=res.replay, anticipo=res.anticipo,
    )
