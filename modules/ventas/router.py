"""Router de ventas: valida, resuelve permisos y delega en el servicio (sin lógica de negocio).

POST /ventas es idempotente (header Idempotency-Key). GET /events es el stream SSE de la empresa.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from core.auth import Principal, get_current_user, require_role
from core.db.session import get_tenant_db
from core.events.sse import tenant_event_stream
from modules.ventas.errors import LineaInvalida, ProductoNoEncontrado, StockInsuficiente
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaLeer
from modules.ventas.service import VentaService

router = APIRouter(tags=["ventas"])


@router.post("/ventas", response_model=VentaLeer, status_code=status.HTTP_201_CREATED)
async def crear_venta(
    payload: VentaCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> VentaLeer:
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})

    service = VentaService(SqlVentasRepository(session))
    try:
        resultado = await service.registrar_venta(payload, vendedor_id=user.user_id)
    except ProductoNoEncontrado as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except StockInsuficiente as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except LineaInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    if resultado.replay:
        response.status_code = status.HTTP_200_OK  # idempotencia: ya existía
    return resultado.venta


@router.get("/events")
async def events(
    request: Request,
    _user: Principal = Depends(get_current_user),
) -> EventSourceResponse:
    return EventSourceResponse(tenant_event_stream(request.state.tenant))
