"""Router de catálogo e inventario (api-contract.md). Lecturas: vendedor; ajuste: admin.

Pack `pos` (ADR 0008): el inventario dejó de ser núcleo; sin la capacidad `pos`, todo el router
responde 404. La lógica vive en InventarioService; aquí solo se valida y se mapea a HTTP.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.inventario.errors import (
    AjusteDejaStockNegativo,
    CodigoDuplicado,
    ProductoInexistente,
    ProveedorInexistente,
)
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.schemas import (
    AjusteCrear,
    AjusteLeer,
    ConteoCrear,
    ConteoLeer,
    KardexItem,
    PrecioLeer,
    ProductoActualizar,
    ProductoCrear,
    ProductoLeer,
    StockLeer,
)
from modules.inventario.service import InventarioService

router = APIRouter(tags=["inventario"], dependencies=[Depends(require_feature("pos"))])


def _service(session: AsyncSession) -> InventarioService:
    return InventarioService(SqlInventarioRepository(session))


@router.get("/productos", response_model=list[ProductoLeer])
async def listar_productos(
    q: str | None = Query(default=None, description="Búsqueda fuzzy/FTS de 3 capas"),
    categoria: str | None = None,
    activo: bool | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[ProductoLeer]:
    repo = SqlInventarioRepository(session)
    if q:
        resultado = await InventarioService(repo).buscar(q, limite=limite)
        ids = [c.producto_id for c in resultado.coincidencias]
        if not ids:
            return []
        productos = {p.id: p for p in await repo.listar_productos(ids=ids, limite=limite)}
        return [ProductoLeer.model_validate(productos[i]) for i in ids if i in productos]
    productos = await repo.listar_productos(
        categoria=categoria, activo=activo, limite=limite, offset=offset
    )
    return [ProductoLeer.model_validate(p) for p in productos]


@router.post("/productos", response_model=ProductoLeer, status_code=status.HTTP_201_CREATED)
async def crear_producto(
    payload: ProductoCrear,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
) -> ProductoLeer:
    try:
        producto = await _service(session).crear_producto(payload, usuario_id=user.user_id)
    except CodigoDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProveedorInexistente as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ProductoLeer.model_validate(producto)


@router.get("/productos/categorias", response_model=list[str])
async def listar_categorias(
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[str]:
    """Categorías existentes (DISTINCT) para el select de categoría del modal. Declarado antes de
    `/productos/{producto_id}` para que 'categorias' no se interprete como un id."""
    return await SqlInventarioRepository(session).categorias_distinct()


@router.get("/productos/{producto_id}", response_model=ProductoLeer)
async def obtener_producto(
    producto_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> ProductoLeer:
    producto = await SqlInventarioRepository(session).obtener_producto(producto_id)
    if producto is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Producto {producto_id} no existe")
    return ProductoLeer.model_validate(producto)


@router.put("/productos/{producto_id}", response_model=ProductoLeer)
async def actualizar_producto(
    producto_id: int,
    payload: ProductoActualizar,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> ProductoLeer:
    try:
        producto = await _service(session).actualizar_producto(producto_id, payload)
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CodigoDuplicado as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProveedorInexistente as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return ProductoLeer.model_validate(producto)


@router.delete("/productos/{producto_id}")
async def eliminar_producto(
    producto_id: int,
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("admin")),
) -> dict[str, object]:
    """Soft delete: el producto queda inactivo (no se borra; lo referencian ventas)."""
    try:
        await _service(session).eliminar_producto(producto_id)
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"producto_id": producto_id, "activo": False}


@router.get("/productos/{producto_id}/precio", response_model=PrecioLeer)
async def precio_producto(
    producto_id: int,
    cantidad: Decimal = Query(gt=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> PrecioLeer:
    try:
        calc = await _service(session).calcular_precio(producto_id, cantidad)
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return PrecioLeer(
        producto_id=calc.producto_id, cantidad=calc.cantidad,
        precio_unitario=calc.precio_unitario, total=calc.total, regla=calc.regla,
    )


@router.get("/inventario/stock", response_model=list[StockLeer])
async def listar_stock(
    bajo: bool = False,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[StockLeer]:
    filas = await SqlInventarioRepository(session).listar_stock(
        solo_bajo=bajo, limite=limite, offset=offset
    )
    return [StockLeer(**fila) for fila in filas]


@router.post("/inventario/ajuste", response_model=AjusteLeer, status_code=status.HTTP_201_CREATED)
async def ajustar_stock(
    payload: AjusteCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AjusteLeer:
    try:
        res = await _service(session).ajustar(
            producto_id=payload.producto_id, delta=payload.cantidad, motivo=payload.motivo,
            usuario_id=user.user_id, idempotency_key=idempotency_key,
        )
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except AjusteDejaStockNegativo as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    if res.replay:
        response.status_code = status.HTTP_200_OK
    return AjusteLeer(
        producto_id=res.producto_id, movimiento_id=res.movimiento_id, delta=res.delta,
        stock_actual=res.stock_actual, replay=res.replay,
    )


@router.post("/inventario/conteo", response_model=ConteoLeer, status_code=status.HTTP_201_CREATED)
async def conteo_fisico(
    payload: ConteoCrear,
    response: Response,
    session: AsyncSession = Depends(get_tenant_db),
    user: Principal = Depends(require_role("admin")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConteoLeer:
    """Conteo físico (set-to-absolute): fija el stock a la cantidad real contada. delta 0 → no-op.

    Reusa la lógica de ajuste (movimiento AJUSTE del delta calculado). 404 si el producto no existe.
    """
    try:
        res = await _service(session).contar(
            producto_id=payload.producto_id, cantidad_contada=payload.cantidad_contada,
            motivo=payload.motivo, usuario_id=user.user_id, idempotency_key=idempotency_key,
        )
    except ProductoInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    if res.replay:
        response.status_code = status.HTTP_200_OK
    return ConteoLeer(
        producto_id=res.producto_id, movimiento_id=res.movimiento_id, delta=res.delta,
        stock_actual=res.stock_actual, replay=res.replay,
    )


@router.get("/inventario/kardex/{producto_id}", response_model=list[KardexItem])
async def kardex(
    producto_id: int,
    limite: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_tenant_db),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[KardexItem]:
    movimientos = await SqlInventarioRepository(session).kardex(
        producto_id, limite=limite, offset=offset
    )
    return [KardexItem.model_validate(m) for m in movimientos]
