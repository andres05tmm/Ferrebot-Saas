"""Routers de caja (`/caja/*`) y gastos (`/gastos`). RBAC: vendedor (api-contract.md).

Feature fina `caja` (ADR 0021, antes pack `pos`): sin la capacidad, ambos routers responden 404.
La lógica vive en CajaService; aquí se valida, se resuelve permiso y se mapea a HTTP.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.caja.config import get_caja_obligatoria
from modules.caja.errors import (
    CajaNoAbierta,
    GastoInexistente,
    GastoNoPendiente,
    ObraNoImputable,
    RecurrenteInexistente,
)
from modules.caja.repository import SqlCajaRepository
from modules.caja.schemas import (
    AperturaCrear,
    ArqueoLeer,
    CajaLeer,
    CierreCrear,
    EstadoCajaLeer,
    GastoCrear,
    GastoImputacionPatch,
    GastoLeer,
    GastoRechazar,
    MovimientoCrear,
    MovimientoLeer,
    RecurrenteGuardar,
    RecurrenteLeer,
)
from modules.caja.service import CajaService
from modules.proveedores.errors import AbonoInvalido, FacturaProveedorInexistente
from modules.proveedores.repository import SqlProveedoresRepository

router = APIRouter(tags=["caja"], dependencies=[Depends(require_feature("caja"))])
gastos_router = APIRouter(tags=["gastos"], dependencies=[Depends(require_feature("caja"))])


def _service(session: AsyncSession) -> CajaService:
    # Cablea el repo de proveedores (misma sesión) para el vínculo gasto→CxP (ADR 0028).
    return CajaService(SqlCajaRepository(session), SqlProveedoresRepository(session))


@router.get("/caja/estado", response_model=EstadoCajaLeer)
async def caja_estado(
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> EstadoCajaLeer:
    """Estado liviano para el guard del POS: ¿hay caja abierta? Siempre 200 (`abierta=false` es estado,
    no error). En modo empresa mira LA caja de la empresa; si no, la del usuario."""
    caja = await _service(session).actual(user.user_id, modo_empresa=modo_empresa)
    if caja is None:
        return EstadoCajaLeer(abierta=False)
    return EstadoCajaLeer(
        abierta=True, caja_id=caja.id, saldo_inicial=caja.saldo_inicial,
        fecha_apertura=caja.fecha_apertura,
    )


@router.get("/caja/actual", response_model=CajaLeer)
async def caja_actual(
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> CajaLeer:
    caja = await _service(session).actual(user.user_id, modo_empresa=modo_empresa)
    if caja is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay caja abierta")
    return CajaLeer.model_validate(caja)


@router.get("/caja/arqueo", response_model=ArqueoLeer)
async def caja_arqueo(
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> ArqueoLeer:
    """Cuadre en vivo de la caja abierta del usuario (componentes + saldo esperado). Caja cerrada → estado
    'cerrada' con los componentes en 0 (200, no 404: el panel siempre pinta el estado). En modo empresa
    (`caja_obligatoria`) el arqueo es del cajón compartido: suma el efectivo de TODOS los vendedores."""
    a = await _service(session).arqueo(user.user_id, modo_empresa=modo_empresa)
    if a is None:
        return ArqueoLeer(estado="cerrada")
    return ArqueoLeer(
        estado="abierta", caja_id=a.caja.id, fecha_apertura=a.caja.fecha_apertura,
        saldo_inicial=a.caja.saldo_inicial, ventas_efectivo=a.ventas_efectivo,
        ingresos=a.ingresos, egresos=a.egresos, saldo_esperado=a.saldo_esperado,
    )


@router.get("/caja/movimientos", response_model=list[MovimientoLeer])
async def listar_movimientos_caja(
    limite: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> list[MovimientoLeer]:
    """Movimientos del turno abierto, más reciente primero (incluye el egreso que postea cada gasto:
    `referencia='gasto:<id>'`). Caja cerrada → lista vacía (200, igual que el arqueo)."""
    movs = await _service(session).movimientos(
        user.user_id, modo_empresa=modo_empresa, limite=limite
    )
    return [MovimientoLeer.model_validate(m) for m in movs]


@router.post("/caja/apertura", response_model=CajaLeer, status_code=status.HTTP_201_CREATED)
async def abrir_caja(
    payload: AperturaCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> CajaLeer:
    res = await _service(session).abrir(
        usuario_id=user.user_id, saldo_inicial=payload.saldo_inicial, modo_empresa=modo_empresa
    )
    if res.replay:
        response.status_code = status.HTTP_200_OK   # ya tenía caja abierta
    return CajaLeer.model_validate(res.caja)


@router.post("/caja/cierre", response_model=CajaLeer)
async def cerrar_caja(
    payload: CierreCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> CajaLeer:
    try:
        caja = await _service(session).cerrar(
            usuario_id=user.user_id, saldo_contado=payload.saldo_contado, modo_empresa=modo_empresa
        )
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return CajaLeer.model_validate(caja)


@router.post("/caja/movimiento", response_model=MovimientoLeer, status_code=status.HTTP_201_CREATED)
async def registrar_movimiento(
    payload: MovimientoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MovimientoLeer:
    try:
        res = await _service(session).registrar_movimiento(
            usuario_id=user.user_id, tipo=payload.tipo, monto=payload.monto,
            concepto=payload.concepto, idempotency_key=idempotency_key,
            modo_empresa=modo_empresa,
        )
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return MovimientoLeer.model_validate(res.movimiento)


@gastos_router.post("/gastos", response_model=GastoLeer, status_code=status.HTTP_201_CREATED)
async def registrar_gasto(
    payload: GastoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> GastoLeer:
    try:
        res = await _service(session).registrar_gasto(
            modo_empresa=modo_empresa,
            usuario_id=user.user_id, categoria=payload.categoria, monto=payload.monto,
            concepto=payload.concepto, idempotency_key=idempotency_key,
            tipo_egreso=payload.tipo_egreso, recurrente_id=payload.recurrente_id,
            proveedor_id=payload.proveedor_id, factura_proveedor_id=payload.factura_proveedor_id,
            obra_id=payload.obra_id, maquina_id=payload.maquina_id,
            categoria_gasto=payload.categoria_gasto, metodo_pago=payload.metodo_pago,
            numero_referencia=payload.numero_referencia, comprobante_url=payload.comprobante_url,
            origen_registro=payload.origen_registro, telegram_user_id=payload.telegram_user_id,
            telegram_message_id=payload.telegram_message_id, requiere_revision=payload.requiere_revision,
        )
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ObraNoImputable as exc:
        codigo = status.HTTP_404_NOT_FOUND if exc.motivo == "inexistente" else status.HTTP_409_CONFLICT
        raise HTTPException(codigo, str(exc)) from exc
    except (FacturaProveedorInexistente, RecurrenteInexistente) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AbonoInvalido as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return GastoLeer.model_validate(res.gasto)


@gastos_router.get("/gastos/revision", response_model=list[GastoLeer])
async def bandeja_revision(
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> list[GastoLeer]:
    """Bandeja de revisión (spec 09): gastos que el bot importó con baja confianza (`requiere_revision`).
    Acción de supervisión → admin."""
    gastos = await _service(session).listar_revision(limite=limite, offset=offset)
    return [GastoLeer.model_validate(g) for g in gastos]


@gastos_router.post("/gastos/{gasto_id}/aprobar", response_model=GastoLeer)
async def aprobar_gasto(
    gasto_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> GastoLeer:
    """Aprueba un gasto de la bandeja (baja `requiere_revision`). Idempotente; 404 si el id no existe;
    409 si fue rechazado (no se resucita: su reversa ya está asentada)."""
    try:
        gasto = await _service(session).aprobar_gasto(gasto_id)
    except GastoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except GastoNoPendiente as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return GastoLeer.model_validate(gasto)


@gastos_router.post("/gastos/{gasto_id}/rechazar", response_model=GastoLeer)
async def rechazar_gasto(
    gasto_id: int,
    payload: GastoRechazar,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> GastoLeer:
    """Rechaza un gasto PENDIENTE de la bandeja: devuelve la plata a caja con un movimiento INVERSO
    (ingreso por el monto exacto) y lo marca anulado. Idempotente (re-rechazar = replay). Admin.
    404 si no existe; 409 si no está pendiente o no hay caja abierta para la reversa."""
    try:
        gasto = await _service(session).rechazar_gasto(
            gasto_id, usuario_id=user.user_id, motivo=payload.motivo, modo_empresa=modo_empresa
        )
    except GastoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (GastoNoPendiente, CajaNoAbierta) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return GastoLeer.model_validate(gasto)


@gastos_router.patch("/gastos/{gasto_id}/imputacion", response_model=GastoLeer)
async def editar_imputacion_gasto(
    gasto_id: int,
    payload: GastoImputacionPatch,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> GastoLeer:
    """Re-imputa un gasto PENDIENTE antes de aprobarlo (obra/máquina/categoría/concepto — nunca el
    monto). Admin. 404 si no existe; 409 si ya es definitivo o la obra está liquidada."""
    try:
        gasto = await _service(session).editar_imputacion(
            gasto_id, payload.model_dump(exclude_unset=True)
        )
    except GastoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except GastoNoPendiente as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ObraNoImputable as exc:
        codigo = status.HTTP_404_NOT_FOUND if exc.motivo == "inexistente" else status.HTTP_409_CONFLICT
        raise HTTPException(codigo, str(exc)) from exc
    return GastoLeer.model_validate(gasto)


@gastos_router.post("/gastos/{gasto_id}/anular", response_model=GastoLeer)
async def anular_gasto(
    gasto_id: int,
    payload: GastoRechazar,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> GastoLeer:
    """Anula un gasto YA REGISTRADO (0071): devuelve la plata a la caja con un movimiento inverso y lo
    marca anulado. Es el arreglo de un gasto mal digitado — antes solo se podían rechazar los de la
    bandeja del bot y un error del dueño quedaba en los números para siempre. Admin, idempotente.
    404 si no existe; 409 si generó un abono a CxP o no hay caja abierta para la reversa."""
    try:
        gasto = await _service(session).rechazar_gasto(
            gasto_id, usuario_id=user.user_id, motivo=payload.motivo,
            modo_empresa=modo_empresa, exigir_pendiente=False,
        )
    except GastoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (GastoNoPendiente, CajaNoAbierta) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return GastoLeer.model_validate(gasto)


@gastos_router.get("/gastos/recurrentes", response_model=list[RecurrenteLeer])
async def listar_recurrentes(
    desde: datetime,
    hasta: datetime,
    incluir_inactivos: bool = False,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[RecurrenteLeer]:
    """Checklist del mes: lo que se paga siempre, con el pago que ya lo saldó en la ventana (o nada).
    El rango lo manda el cliente ya en hora Colombia (mismo contrato que `GET /gastos`)."""
    filas = await _service(session).listar_recurrentes(
        inicio=desde, fin=hasta, solo_activos=not incluir_inactivos
    )
    salida = []
    for recurrente, pago in filas:
        leer = RecurrenteLeer.model_validate(recurrente)
        if pago is not None:
            gasto_id, fecha, monto = pago
            leer = leer.model_copy(
                update={"gasto_id": gasto_id, "pagado_en": fecha, "monto_pagado": monto}
            )
        salida.append(leer)
    return salida


@gastos_router.post(
    "/gastos/recurrentes", response_model=RecurrenteLeer, status_code=status.HTTP_201_CREATED
)
async def crear_recurrente(
    payload: RecurrenteGuardar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> RecurrenteLeer:
    """Da de alta un gasto que se repite todos los meses (arriendo, luz, nómina). Admin."""
    recurrente = await _service(session).crear_recurrente(payload.model_dump())
    return RecurrenteLeer.model_validate(recurrente)


@gastos_router.put("/gastos/recurrentes/{recurrente_id}", response_model=RecurrenteLeer)
async def actualizar_recurrente(
    recurrente_id: int,
    payload: RecurrenteGuardar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> RecurrenteLeer:
    """Edita el recurrente (o lo baja con `activo=false` — nunca se borra: los gastos que ya lo
    saldaron conservan su vínculo). Admin. 404 si no existe."""
    try:
        recurrente = await _service(session).actualizar_recurrente(
            recurrente_id, payload.model_dump()
        )
    except RecurrenteInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return RecurrenteLeer.model_validate(recurrente)


@gastos_router.get("/gastos", response_model=list[GastoLeer])
async def listar_gastos(
    desde: datetime | None = None,
    hasta: datetime | None = None,
    categoria: str | None = None,
    tipo_egreso: str | None = None,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[GastoLeer]:
    gastos = await SqlCajaRepository(session).listar_gastos(
        desde=desde, hasta=hasta, categoria=categoria, tipo_egreso=tipo_egreso,
        limite=limite, offset=offset,
    )
    return [GastoLeer.model_validate(g) for g in gastos]
