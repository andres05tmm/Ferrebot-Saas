"""Router del pack Agenda (backend del dashboard). Gateado por la capacidad `pack_agenda` (404 sin ella).

RBAC: el catálogo y la configuración (servicios, recursos, N:N, disponibilidad, agenda_config) son de
**admin**; ver y gestionar **citas** y bloqueos es de **staff** (vendedor+). La lógica vive en
`AgendaService` (reusa el motor); aquí solo se valida, se mapea a HTTP y se serializa — sin SQL.

Tiempo real: las mutaciones de cita (crear/confirmar/cancelar/reagendar) emiten su evento SSE en el
repositorio (`publish` → pg_notify, acotado al tenant). Igual para la ruta del agente por WhatsApp:
ambas pasan por el MISMO repo, así el dashboard se actualiza en vivo. Fechas en hora Colombia.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.agenda.errors import (
    AgendaError,
    CitaInexistente,
    CitaNoModificable,
    CupoNoDisponible,
    FueraDePoliticaCancelacion,
    ReagendarNoPermitido,
    RecursoInexistente,
    RecursoNoPrestaServicio,
    ServicioInexistente,
)
from modules.agenda.gcal import calendar_client_por_defecto
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    AgendaConfigLeer,
    BloqueoCrear,
    BloqueoLeer,
    CitaLeer,
    CitaManualCrear,
    DisponibilidadCrear,
    DisponibilidadLeer,
    ReagendarPayload,
    RecursoCrear,
    RecursoLeer,
    RecursoServicioCrear,
    ServicioCrear,
    ServicioLeer,
    SlotLeer,
)
from modules.agenda.service import AgendaService

# Todo el router exige el flag pack_agenda (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/agenda", tags=["agenda"],
    dependencies=[Depends(require_feature("pack_agenda"))],
)


def get_agenda_service(session: AsyncSession = Depends(get_tenant_db)) -> AgendaService:
    """Arma el `AgendaService` sobre la sesión del tenant (los tests lo overridean).

    Pasa el cliente de Google Calendar de plataforma (o None si no hay SA configurado): así las
    acciones del dashboard (alta/cancelar/reagendar) también espejan al calendario, best-effort.
    """
    return AgendaService(SqlAgendaRepository(session), gcal=calendar_client_por_defecto())


def _a_http(exc: AgendaError) -> HTTPException:
    """Mapea un error de dominio del pack a su status HTTP (defaults seguros)."""
    if isinstance(exc, (ServicioInexistente, RecursoInexistente, CitaInexistente)):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, CupoNoDisponible):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {"detail": str(exc), "alternativas": [a.isoformat() for a in exc.alternativas]},
        )
    if isinstance(exc, (CitaNoModificable, ReagendarNoPermitido, FueraDePoliticaCancelacion)):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, RecursoNoPrestaServicio):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@contextmanager
def _mapear() -> Iterator[None]:
    """Traduce cualquier `AgendaError` del bloque a su HTTPException."""
    try:
        yield
    except AgendaError as exc:
        raise _a_http(exc) from exc


# --- servicios (catálogo: admin escribe, staff lee) --------------------------
@router.get("/servicios", response_model=list[ServicioLeer])
async def listar_servicios(
    incluir_inactivos: bool = Query(default=False),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ServicioLeer]:
    return await service.listar_servicios(solo_activos=not incluir_inactivos)


@router.post("/servicios", response_model=ServicioLeer, status_code=status.HTTP_201_CREATED)
async def crear_servicio(
    payload: ServicioCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> ServicioLeer:
    return await service.crear_servicio(payload)


@router.get("/servicios/{servicio_id}", response_model=ServicioLeer)
async def obtener_servicio(
    servicio_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> ServicioLeer:
    with _mapear():
        return await service.obtener_servicio(servicio_id)


@router.put("/servicios/{servicio_id}", response_model=ServicioLeer)
async def actualizar_servicio(
    servicio_id: int,
    payload: ServicioCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> ServicioLeer:
    with _mapear():
        return await service.actualizar_servicio(servicio_id, payload)


@router.delete("/servicios/{servicio_id}", response_model=ServicioLeer)
async def desactivar_servicio(
    servicio_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> ServicioLeer:
    """Soft-delete: desactiva el servicio (no se borra; las citas lo siguen referenciando)."""
    with _mapear():
        return await service.desactivar_servicio(servicio_id)


@router.get("/servicios/{servicio_id}/recursos", response_model=list[RecursoLeer])
async def recursos_de_servicio(
    servicio_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[RecursoLeer]:
    with _mapear():
        return await service.recursos_de_servicio(servicio_id)


# --- recursos ----------------------------------------------------------------
@router.get("/recursos", response_model=list[RecursoLeer])
async def listar_recursos(
    incluir_inactivos: bool = Query(default=False),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[RecursoLeer]:
    return await service.listar_recursos(solo_activos=not incluir_inactivos)


@router.post("/recursos", response_model=RecursoLeer, status_code=status.HTTP_201_CREATED)
async def crear_recurso(
    payload: RecursoCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> RecursoLeer:
    return await service.crear_recurso(payload)


@router.get("/recursos/{recurso_id}", response_model=RecursoLeer)
async def obtener_recurso(
    recurso_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> RecursoLeer:
    with _mapear():
        return await service.obtener_recurso(recurso_id)


@router.put("/recursos/{recurso_id}", response_model=RecursoLeer)
async def actualizar_recurso(
    recurso_id: int,
    payload: RecursoCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> RecursoLeer:
    with _mapear():
        return await service.actualizar_recurso(recurso_id, payload)


@router.delete("/recursos/{recurso_id}", response_model=RecursoLeer)
async def desactivar_recurso(
    recurso_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> RecursoLeer:
    with _mapear():
        return await service.desactivar_recurso(recurso_id)


@router.get("/recursos/{recurso_id}/disponibilidad", response_model=list[DisponibilidadLeer])
async def listar_disponibilidad(
    recurso_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[DisponibilidadLeer]:
    with _mapear():
        return await service.listar_disponibilidad(recurso_id)


# --- recurso_servicio (N:N) --------------------------------------------------
@router.post("/recurso-servicio", status_code=status.HTTP_204_NO_CONTENT)
async def asignar_recurso_servicio(
    payload: RecursoServicioCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    with _mapear():
        await service.asignar_recurso_servicio(
            recurso_id=payload.recurso_id, servicio_id=payload.servicio_id
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/recurso-servicio", status_code=status.HTTP_204_NO_CONTENT)
async def desasignar_recurso_servicio(
    recurso_id: int = Query(...),
    servicio_id: int = Query(...),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    await service.desasignar_recurso_servicio(recurso_id=recurso_id, servicio_id=servicio_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- disponibilidad ----------------------------------------------------------
@router.post("/disponibilidad", response_model=DisponibilidadLeer, status_code=status.HTTP_201_CREATED)
async def crear_disponibilidad(
    payload: DisponibilidadCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> DisponibilidadLeer:
    with _mapear():
        return await service.crear_disponibilidad(payload)


@router.delete("/disponibilidad/{disponibilidad_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_disponibilidad(
    disponibilidad_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    if not await service.eliminar_disponibilidad(disponibilidad_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Disponibilidad no encontrada")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- bloqueos (operativo: staff) ---------------------------------------------
@router.get("/bloqueos", response_model=list[BloqueoLeer])
async def listar_bloqueos(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[BloqueoLeer]:
    return await service.listar_bloqueos(desde=desde, hasta=hasta)


@router.post("/bloqueos", response_model=BloqueoLeer, status_code=status.HTTP_201_CREATED)
async def crear_bloqueo(
    payload: BloqueoCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> BloqueoLeer:
    with _mapear():
        return await service.crear_bloqueo(payload)


@router.delete("/bloqueos/{bloqueo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def eliminar_bloqueo(
    bloqueo_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> Response:
    if not await service.eliminar_bloqueo(bloqueo_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bloqueo no encontrado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- agenda_config (fila única) ----------------------------------------------
@router.get("/config", response_model=AgendaConfigLeer)
async def obtener_config(
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> AgendaConfigLeer:
    config = await service.obtener_config()
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "La agenda aún no está configurada")
    return config


@router.put("/config", response_model=AgendaConfigLeer)
async def guardar_config(
    payload: AgendaConfigCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("admin")),
) -> AgendaConfigLeer:
    return await service.guardar_config(payload)


# --- citas (lectura + acciones del negocio) ----------------------------------
@router.get("/citas", response_model=list[CitaLeer])
async def listar_citas(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None),
    recurso_id: int | None = Query(default=None),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[CitaLeer]:
    """Citas del rango (default hoy → +30 días, hora Colombia), con filtros de estado y recurso."""
    return await service.listar_citas(desde=desde, hasta=hasta, estado=estado, recurso_id=recurso_id)


@router.get("/slots", response_model=list[SlotLeer])
async def consultar_slots(
    servicio_id: int = Query(...),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    recurso_id: int | None = Query(default=None),
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[SlotLeer]:
    """Cupos libres de un servicio (para el form de alta manual). Reusa el motor."""
    with _mapear():
        slots = await service.calcular_disponibilidad(
            servicio_id, desde=desde, hasta=hasta, recurso_id=recurso_id
        )
    return [SlotLeer(inicio=s.inicio, recurso_id=s.recurso_id) for s in slots]


@router.post("/citas", response_model=CitaLeer, status_code=status.HTTP_201_CREATED)
async def crear_cita_manual(
    payload: CitaManualCrear,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CitaLeer:
    """Alta de cita desde el dashboard (origen=dashboard). El motor valida el cupo y toma el lock."""
    with _mapear():
        resultado = await service.validar_y_agendar(
            servicio_id=payload.servicio_id, recurso_id=payload.recurso_id, inicio=payload.inicio,
            cliente_nombre=payload.cliente_nombre, cliente_telefono=payload.cliente_telefono,
            origen="dashboard", notas=payload.notas,
        )
    return CitaLeer.model_validate(resultado.cita)


@router.get("/citas/{cita_id}", response_model=CitaLeer)
async def obtener_cita(
    cita_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CitaLeer:
    with _mapear():
        return await service.obtener_cita(cita_id)


@router.post("/citas/{cita_id}/confirmar", response_model=CitaLeer)
async def confirmar_cita(
    cita_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CitaLeer:
    """Confirma una cita pendiente (negocios con modo_confirmacion=manual)."""
    with _mapear():
        return await service.confirmar(cita_id)


@router.post("/citas/{cita_id}/cancelar", response_model=CitaLeer)
async def cancelar_cita(
    cita_id: int,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CitaLeer:
    """Cancela una cita desde el dashboard (sin la política de cancelación del cliente)."""
    with _mapear():
        return await service.cancelar_negocio(cita_id)


@router.post("/citas/{cita_id}/reagendar", response_model=CitaLeer)
async def reagendar_cita(
    cita_id: int,
    payload: ReagendarPayload,
    service: AgendaService = Depends(get_agenda_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CitaLeer:
    """Reagenda una cita desde el dashboard (revalida el cupo con lock; sin política ni teléfono)."""
    with _mapear():
        return await service.reagendar_negocio(cita_id, payload.nuevo_inicio)
