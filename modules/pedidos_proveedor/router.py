"""Router de pedidos a proveedor (`/pedidos-proveedor*`). Feature `pedidos_proveedor` (dep `inventario`).

Registrar/recibir/cancelar es de VENDEDOR: recibir mercancía es un acto operativo del mostrador (la
empleada recibe al proveedor); la analítica de métricas también la ve el vendedor (el semáforo del
cronómetro es información operativa, no financiera sensible). La lógica vive en
PedidosProveedorService; aquí solo se valida, se resuelve permiso y se mapea a HTTP.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.caja.config import get_caja_obligatoria
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.service import ComprasService
from modules.inventario.errors import ProductoInexistente
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.errors import (
    IdempotenciaConflicto,
    PedidoInexistente,
    PedidoNoEditable,
    RecepcionInvalida,
)
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import (
    MetricasProveedor,
    PedidoCrear,
    PedidoEditar,
    PedidoLeer,
    RecepcionLeer,
    RecibirPedido,
)
from modules.pedidos_proveedor.service import PedidosProveedorService
from modules.proveedores.repository import SqlProveedoresRepository

router = APIRouter(
    tags=["pedidos-proveedor"], dependencies=[Depends(require_feature("pedidos_proveedor"))]
)


def _service(session: AsyncSession) -> PedidosProveedorService:
    """Todos los servicios atados a la MISMA sesión del tenant: la recepción es una transacción."""
    return PedidosProveedorService(
        SqlPedidosProveedorRepository(session),
        compras=ComprasService(SqlComprasRepository(session)),
        compras_repo=SqlComprasRepository(session),
        proveedores=SqlProveedoresRepository(session),
        caja=CajaService(SqlCajaRepository(session)),
        inventario=InventarioService(SqlInventarioRepository(session)),
    )


@router.post("/pedidos-proveedor", response_model=PedidoLeer, status_code=status.HTTP_201_CREATED)
async def crear_pedido(
    payload: PedidoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PedidoLeer:
    """Registra el pedido al proveedor — AQUÍ ARRANCA EL CRONÓMETRO de lead time.

    Captura flexible: descripción + monto estimado bastan (el detalle preciso llega con la
    mercancía). `anticipo` + `anticipo_desde_caja` egresa el pago adelantado del cajón (exige caja
    abierta). Idempotente por `Idempotency-Key`."""
    if payload.idempotency_key is None and idempotency_key:
        payload = payload.model_copy(update={"idempotency_key": idempotency_key})
    try:
        res = await _service(session).crear(
            payload, usuario_id=user.user_id, modo_empresa=modo_empresa
        )
    except IdempotenciaConflicto as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except CajaNoAbierta as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return res.pedido


@router.get("/pedidos-proveedor", response_model=list[PedidoLeer])
async def listar_pedidos(
    estado: str | None = Query(default=None, pattern="^(pedido|recibido|cancelado)$"),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[PedidoLeer]:
    """Pedidos (default: todos), con el cronómetro derivado: `horas_transcurridas` (en camino),
    `lead_time_horas` (recibidos) y `promedio_proveedor_horas` (semáforo vs histórico)."""
    return await _service(session).listar(estado=estado)


@router.get("/pedidos-proveedor/metricas", response_model=list[MetricasProveedor])
async def metricas_proveedores(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[MetricasProveedor]:
    """Lead time por proveedor: promedio histórico, última entrega, pedidos en camino y el más
    viejo esperando. Declarado antes de `/pedidos-proveedor/{id}` (que 'metricas' no sea un id)."""
    return await _service(session).metricas()


@router.put("/pedidos-proveedor/{pedido_id}", response_model=PedidoLeer)
async def editar_pedido(
    pedido_id: int,
    payload: PedidoEditar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    """Edita un pedido EN CAMINO (descripción, monto, fecha estimada, líneas). 409 si ya no lo está."""
    try:
        return await _service(session).editar(pedido_id, payload)
    except PedidoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PedidoNoEditable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/pedidos-proveedor/{pedido_id}/cancelar", response_model=PedidoLeer)
async def cancelar_pedido(
    pedido_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
) -> PedidoLeer:
    """Cancela un pedido en camino. Un anticipo entregado NO se revierte automático: queda la nota
    para gestionarlo con el proveedor."""
    try:
        return await _service(session).cancelar(pedido_id, usuario_id=user.user_id)
    except PedidoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PedidoNoEditable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/pedidos-proveedor/{pedido_id}/recibir", response_model=RecepcionLeer)
async def recibir_pedido(
    pedido_id: int,
    payload: RecibirPedido,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("vendedor")),
    modo_empresa: bool = Depends(get_caja_obligatoria),
) -> RecepcionLeer:
    """LLEGÓ LA MERCANCÍA — para el cronómetro y asienta todo en UNA transacción: compra real
    (ENTRADA + costo promedio), deuda (crédito/remanente con abono automático del anticipo), pago de
    contado desde caja, y el cuadre de inventario progresivo (`cantidad_fisica`). Reintento con la
    misma sustancia → 200 replay; con números distintos → 409."""
    try:
        res = await _service(session).recibir(
            pedido_id, payload, usuario_id=user.user_id, modo_empresa=modo_empresa
        )
    except PedidoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (PedidoNoEditable, CajaNoAbierta) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except RecepcionInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if res.replay:
        response.status_code = status.HTTP_200_OK
    return res
