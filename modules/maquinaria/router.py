"""Router de maquinaria (vertical construcción). Gate de capacidad `maquinaria` (feature-flags.md): sin
ella, todo el router responde 404 (como si no existiera). Lecturas: rol `vendedor`; mutaciones: `admin`
(calca la partición de `modules/inventario/router.py`). La lógica vive en `MaquinariaService`; aquí solo
se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import get_capacidades, require_feature
from core.db.session import get_tenant_db
from core.tenancy.catalogo import expandir_metapacks
from modules.cartera.service import construir_cartera_service
from modules.maquinaria.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    CodigoMaquinaDuplicado,
    MantenimientoInexistente,
    MaquinaInexistente,
    ObraNoAsignable,
    OperadorInexistente,
    SinAsignacionActiva,
)
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import (
    AsignacionMaquinaActualizar,
    AsignacionMaquinaCrear,
    AsignacionMaquinaObraLeer,
    EstadoMaquina,
    MantenimientoActualizar,
    MantenimientoCrear,
    MantenimientoLeer,
    MaquinaActualizar,
    MaquinaCrear,
    MaquinaLeer,
    RegistroHorasCrear,
    RegistroHorasMaquinaLeer,
    RegistroHorasResultado,
    TurnoLeer,
)
from modules.maquinaria.service import MaquinariaService

router = APIRouter(tags=["maquinaria"], dependencies=[Depends(require_feature("maquinaria"))])


def _service(session: AsyncSession, cartera=None) -> MaquinariaService:
    """Arma el servicio; `cartera` se inyecta SOLO en el write de horas de tenants con `cartera_alquiler`
    (la señal que enciende el seam de Fase 5). El resto de endpoints no lo necesita."""
    return MaquinariaService(SqlMaquinasRepository(session), cartera)


def get_maquinaria_service(session: AsyncSession = Depends(get_tenant_db)) -> MaquinariaService:
    """Arma el `MaquinariaService` sobre la sesión del tenant (los tests de wiring lo overridean con un
    fake, sin red ni Postgres — patrón `get_obras_service`). Sin cartera: los endpoints de mantenimiento
    no tocan el seam de Fase 5."""
    return MaquinariaService(SqlMaquinasRepository(session))


@router.get("/maquinas", response_model=list[MaquinaLeer])
async def listar_maquinas(
    estado: EstadoMaquina | None = Query(default=None, description="Filtra por estado de la máquina"),
    q: str | None = Query(default=None, description="Filtra por código o nombre (ILIKE)"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MaquinaLeer]:
    """Máquinas vivas (no eliminadas), filtrables por estado y por texto."""
    maquinas = await _service(session).listar(estado=estado, q=q)
    return [MaquinaLeer.model_validate(m) for m in maquinas]


@router.post("/maquinas", response_model=MaquinaLeer, status_code=status.HTTP_201_CREATED)
async def crear_maquina(
    payload: MaquinaCrear,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> MaquinaLeer:
    """Da de alta una máquina. Código duplicado → 409."""
    try:
        maquina = await _service(session).crear(payload)
    except CodigoMaquinaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.get("/maquinas/{maquina_id}", response_model=MaquinaLeer)
async def obtener_maquina(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> MaquinaLeer:
    try:
        maquina = await _service(session).obtener(maquina_id)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.patch("/maquinas/{maquina_id}", response_model=MaquinaLeer)
async def actualizar_maquina(
    maquina_id: int,
    payload: MaquinaActualizar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> MaquinaLeer:
    """Edición parcial (solo los campos enviados). 404 si no existe; 409 si el código lo usa otra."""
    try:
        maquina = await _service(session).actualizar(maquina_id, payload)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CodigoMaquinaDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return MaquinaLeer.model_validate(maquina)


@router.delete("/maquinas/{maquina_id}")
async def eliminar_maquina(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, object]:
    """Soft delete: la máquina queda con `eliminado_en` (no se borra; la referencian horas/asignaciones)."""
    try:
        await _service(session).eliminar(maquina_id)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"maquina_id": maquina_id, "eliminado": True}


@router.get("/maquinas/{maquina_id}/asignaciones", response_model=list[AsignacionMaquinaObraLeer])
async def listar_asignaciones(
    maquina_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[AsignacionMaquinaObraLeer]:
    """Asignaciones a obra de la máquina (solo lectura)."""
    asignaciones = await _service(session).listar_asignaciones(maquina_id)
    return [AsignacionMaquinaObraLeer.model_validate(a) for a in asignaciones]


def _obra_no_asignable_http(exc: ObraNoAsignable) -> HTTPException:
    """Mapea `ObraNoAsignable` al código del contrato: obra inexistente → 404; LIQUIDADA → 409."""
    codigo = status.HTTP_404_NOT_FOUND if exc.motivo == "inexistente" else status.HTTP_409_CONFLICT
    return HTTPException(codigo, str(exc))


@router.post(
    "/maquinas/{maquina_id}/asignaciones",
    response_model=AsignacionMaquinaObraLeer,
    status_code=status.HTTP_201_CREATED,
)
async def crear_asignacion(
    maquina_id: int,
    payload: AsignacionMaquinaCrear,
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("admin")),
) -> AsignacionMaquinaObraLeer:
    """Asigna la máquina a una obra (Calendario de obra). `fecha_inicio` default hoy Colombia;
    `precio_hora`/`minimo_horas` heredan los de la máquina si no se envían. 404 si la máquina/obra/operador
    no existen; 409 si la obra está LIQUIDADA o el rango se solapa con otra asignación activa."""
    try:
        asig = await service.crear_asignacion(maquina_id, payload)
    except (MaquinaInexistente, OperadorInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ObraNoAsignable as exc:
        raise _obra_no_asignable_http(exc) from exc
    except AsignacionSolapada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AsignacionMaquinaObraLeer.model_validate(asig)


@router.patch(
    "/maquinas/{maquina_id}/asignaciones/{asignacion_id}",
    response_model=AsignacionMaquinaObraLeer,
)
async def actualizar_asignacion(
    maquina_id: int,
    asignacion_id: int,
    payload: AsignacionMaquinaActualizar,
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("admin")),
) -> AsignacionMaquinaObraLeer:
    """Edición parcial de una asignación (cerrar, reasignar operador, ajustar tarifa/mínimo). 404 si no
    existe para esa máquina o el operador no existe; 409 si el nuevo rango se solapa con otra activa."""
    try:
        asig = await service.actualizar_asignacion(maquina_id, asignacion_id, payload)
    except (AsignacionInexistente, OperadorInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AsignacionSolapada as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return AsignacionMaquinaObraLeer.model_validate(asig)


@router.get("/maquinas/{maquina_id}/horas", response_model=list[RegistroHorasMaquinaLeer])
async def listar_horas(
    maquina_id: int,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[RegistroHorasMaquinaLeer]:
    """Partes de horas de la máquina (kárdex de operación). Cada parte trae sus `turnos` de rotación
    (`[]` en los partes legacy); se resuelven en UNA consulta batcheada (N+1-free)."""
    servicio = _service(session)
    horas = await servicio.listar_horas(maquina_id, limite=limite, offset=offset)
    turnos = await servicio.turnos_por_registros([h.id for h in horas])
    salida: list[RegistroHorasMaquinaLeer] = []
    for h in horas:
        leer = RegistroHorasMaquinaLeer.model_validate(h)
        leer.turnos = [TurnoLeer(**t) for t in turnos.get(h.id, [])]
        salida.append(leer)
    return salida


@router.post(
    "/maquinas/{maquina_id}/horas",
    response_model=RegistroHorasResultado,
    status_code=status.HTTP_201_CREATED,
)
async def registrar_horas(
    maquina_id: int,
    payload: RegistroHorasCrear,
    session: AsyncSession = Depends(get_tenant_db),
    capacidades: frozenset[str] = Depends(get_capacidades),
    _user: Principal = Depends(require_role("vendedor")),
) -> RegistroHorasResultado:
    """Registra el parte de horas del día de una máquina en una obra, aplicando el mínimo facturable.

    Rol `vendedor` (personal de campo). Devuelve el resumen: horas trabajadas/facturables, si se cubrió el
    mínimo, precio pactado e ingreso. IDEMPOTENTE por `(máquina, obra, fecha)`: reintentar el mismo día NO
    duplica (responde el mismo registro con `replay=true`). 404 si la máquina no existe; 409 si no hay
    asignación activa que cubra la fecha.

    Cartera de alquiler (Fase 5): si el tenant tiene `cartera_alquiler`, se inyecta el servicio de cartera
    para que el seam asiente el consumo de horas como cargo en el ledger de fiados (misma transacción). El
    cargo es idempotente por `registro.id`; sin la capacidad, el registro conserva su comportamiento previo.
    """
    cartera = (
        construir_cartera_service(session)
        if "cartera_alquiler" in expandir_metapacks(capacidades)
        else None
    )
    try:
        resultado = await _service(session, cartera).registrar_horas(maquina_id, payload)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except SinAsignacionActiva as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return RegistroHorasResultado.model_validate(resultado)


# --- Mantenimientos (Fase 1 del cockpit): lecturas vendedor, mutaciones admin -----------------------
# Rutas con segmento estático `/mantenimientos` tras `{maquina_id}`: no colisionan con `/maquinas/{id}`.


@router.get("/maquinas/{maquina_id}/mantenimientos", response_model=list[MantenimientoLeer])
async def listar_mantenimientos(
    maquina_id: int,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MantenimientoLeer]:
    """Mantenimientos de la máquina (más recientes primero). 404 si la máquina no existe."""
    try:
        mantenimientos = await service.listar_mantenimientos(maquina_id, limite=limite, offset=offset)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return [MantenimientoLeer.model_validate(m) for m in mantenimientos]


@router.post(
    "/maquinas/{maquina_id}/mantenimientos",
    response_model=MantenimientoLeer,
    status_code=status.HTTP_201_CREATED,
)
async def crear_mantenimiento(
    maquina_id: int,
    payload: MantenimientoCrear,
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("admin")),
) -> MantenimientoLeer:
    """Registra un mantenimiento de la máquina (fecha default hoy Colombia). NO cambia `maquina.estado`.
    404 si la máquina no existe."""
    try:
        mantenimiento = await service.crear_mantenimiento(maquina_id, payload)
    except MaquinaInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return MantenimientoLeer.model_validate(mantenimiento)


@router.patch(
    "/maquinas/{maquina_id}/mantenimientos/{mantenimiento_id}", response_model=MantenimientoLeer
)
async def actualizar_mantenimiento(
    maquina_id: int,
    mantenimiento_id: int,
    payload: MantenimientoActualizar,
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("admin")),
) -> MantenimientoLeer:
    """Edición parcial de un mantenimiento (solo lo enviado). 404 si no existe para esa máquina."""
    try:
        mantenimiento = await service.actualizar_mantenimiento(
            maquina_id, mantenimiento_id, payload
        )
    except MantenimientoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return MantenimientoLeer.model_validate(mantenimiento)


@router.delete(
    "/maquinas/{maquina_id}/mantenimientos/{mantenimiento_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def eliminar_mantenimiento(
    maquina_id: int,
    mantenimiento_id: int,
    service: MaquinariaService = Depends(get_maquinaria_service),
    _user: Principal = Depends(require_role("admin")),
) -> Response:
    """DELETE duro (la tabla no tiene soft delete). 404 si no existe para esa máquina; 204 si se borró."""
    try:
        await service.eliminar_mantenimiento(maquina_id, mantenimiento_id)
    except MantenimientoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
