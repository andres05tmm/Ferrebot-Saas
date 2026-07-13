"""Router de la operación de máquina EN VIVO (cronómetro + rotación de operadores). Gate de capacidad
`maquinaria` (sin ella todo responde 404). Operar (iniciar/rotar/finalizar) = rol `vendedor` (personal
de campo, igual que registrar horas); anular = `admin` (deshacer). La lógica vive en
`OperacionMaquinaService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import get_capacidades, require_feature
from core.db.session import get_tenant_db
from core.tenancy.catalogo import expandir_metapacks
from modules.cartera.service import construir_cartera_service
from modules.maquinaria.errors import (
    MaquinaInexistente,
    MaquinaNoOperable,
    ObraNoAsignable,
    OperadorInexistente,
    SesionInexistente,
    SesionNoAbierta,
    SesionYaAbierta,
    SinAsignacionActiva,
)
from modules.maquinaria.operacion_service import (
    OperacionMaquinaService,
    construir_operacion_service,
)
from modules.maquinaria.schemas import (
    FinalizarOperacion,
    IniciarOperacion,
    RegistroHorasResultado,
    RotarOperador,
    SesionDetalle,
    SesionLeer,
    TableroSesion,
    TramoDetalle,
)

router = APIRouter(
    tags=["maquinaria-operacion"], dependencies=[Depends(require_feature("maquinaria"))]
)


def get_operacion_service(
    session: AsyncSession = Depends(get_tenant_db),
    capacidades: frozenset[str] = Depends(get_capacidades),
) -> OperacionMaquinaService:
    """Arma el servicio sobre la sesión del tenant. Inyecta la cartera de alquiler SOLO si el tenant tiene
    la capacidad `cartera_alquiler` (la señal que enciende el seam al materializar en `finalizar`); el
    resto de operaciones no la usa. Los tests de wiring lo overridean con un fake."""
    cartera = (
        construir_cartera_service(session)
        if "cartera_alquiler" in expandir_metapacks(capacidades)
        else None
    )
    return construir_operacion_service(session, cartera)


@router.post(
    "/maquinas/{maquina_id}/operacion/iniciar",
    response_model=SesionLeer,
    status_code=status.HTTP_201_CREATED,
)
async def iniciar_operacion(
    maquina_id: int,
    payload: IniciarOperacion,
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> SesionLeer:
    """Activa la máquina: abre la sesión (cronómetro) con su primer tramo. 404 si la máquina/operador no
    existen; 409 si ya hay una sesión abierta o no hay asignación activa que la ponga en obra hoy."""
    try:
        sesion = await service.iniciar(
            maquina_id, obra_id=payload.obra_id, operador_id=payload.operador_id
        )
    except (MaquinaInexistente, OperadorInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (SesionYaAbierta, SinAsignacionActiva, MaquinaNoOperable) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return SesionLeer.model_validate(sesion)


@router.post("/operacion/{sesion_id}/rotar", response_model=SesionLeer)
async def rotar_operador(
    sesion_id: int,
    payload: RotarOperador,
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> SesionLeer:
    """Cambia de operador en vivo (cierra el tramo corriente, abre otro). 404 si la sesión/operador no
    existen; 409 si la sesión no está abierta."""
    try:
        sesion = await service.rotar(sesion_id, payload.operador_id)
    except (SesionInexistente, OperadorInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SesionNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return SesionLeer.model_validate(sesion)


@router.post("/operacion/{sesion_id}/finalizar", response_model=RegistroHorasResultado)
async def finalizar_operacion(
    sesion_id: int,
    payload: FinalizarOperacion,
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> RegistroHorasResultado:
    """Finaliza y MATERIALIZA la sesión en el parte del día (horas por tramo = reloj, ajustables). Reusa
    mínimo facturable, agregación por turnos y seam de cartera. IDEMPOTENTE: re-finalizar responde el
    mismo parte (replay). 404 si no existe; 409 si fue anulada o si la asignación dejó de cubrir la fecha."""
    ajustes = {a.tramo_id: a.horas for a in payload.ajustes}
    try:
        resultado = await service.finalizar(sesion_id, ajustes=ajustes or None)
    except (SesionInexistente, MaquinaInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (SesionNoAbierta, SinAsignacionActiva) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ObraNoAsignable as exc:
        # La obra se liquidó (o desapareció) con la sesión corriendo: la materialización no procede.
        codigo = status.HTTP_404_NOT_FOUND if exc.motivo == "inexistente" else status.HTTP_409_CONFLICT
        raise HTTPException(codigo, str(exc)) from exc
    return RegistroHorasResultado.model_validate(resultado)


@router.post("/operacion/{sesion_id}/anular", response_model=SesionLeer)
async def anular_operacion(
    sesion_id: int,
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("admin")),
) -> SesionLeer:
    """Descarta una sesión abierta (no materializa, no factura). Solo admin (deshacer). 404 si no existe;
    409 si ya estaba finalizada/anulada."""
    try:
        sesion = await service.anular(sesion_id)
    except SesionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SesionNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return SesionLeer.model_validate(sesion)


@router.get("/operacion/tablero", response_model=list[TableroSesion])
async def tablero_operacion(
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[TableroSesion]:
    """Sesiones en curso (máquina/obra/operador actual + inicio) para las tarjetas con cronómetro en vivo."""
    return [TableroSesion(**fila) for fila in await service.tablero()]


# `/operacion/tablero` (estático) se declara ANTES: con `sesion_id:int` "tablero" nunca casa aquí, pero
# el orden lo deja explícito.
@router.get("/operacion/{sesion_id}", response_model=SesionDetalle)
async def obtener_operacion(
    sesion_id: int,
    service: OperacionMaquinaService = Depends(get_operacion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> SesionDetalle:
    """Detalle de una sesión + sus tramos con horas propuestas (para el modal de revisión). 404 si no existe."""
    try:
        detalle = await service.detalle(sesion_id)
    except SesionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    salida = SesionDetalle.model_validate(detalle["sesion"])
    salida.tramos = [TramoDetalle(**t) for t in detalle["tramos"]]
    salida.minimo_horas = detalle.get("minimo_horas")
    return salida
