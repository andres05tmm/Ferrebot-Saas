"""Servicio de inventario: orquesta búsqueda, cálculo de precio y ajuste de stock.

El SQL vive en SqlInventarioRepository; aquí solo va la lógica de dominio (regla de precio,
idempotencia del ajuste, validación de stock negativo). El ajuste corre en una transacción:
lock de la fila de inventario → movimiento AJUSTE → actualización de stock → evento.
"""
from dataclasses import dataclass
from decimal import Decimal

from core.config.timezone import now_co
from modules.inventario.busqueda import BuscadorProductos, ResultadoBusqueda
from modules.inventario.errors import (
    AjusteDejaStockNegativo,
    CodigoDuplicado,
    ProductoInexistente,
    ProveedorInexistente,
)
from modules.inventario.models import Producto
from modules.inventario.precios import (
    EsquemaPrecio,
    FraccionPrecio,
    obtener_precio_para_cantidad,
    regla_para_cantidad,
)
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.schemas import ProductoActualizar, ProductoCrear


def esquema_de(producto: Producto) -> EsquemaPrecio:
    """Construye el esquema de precio que consume el motor a partir del producto ORM."""
    return EsquemaPrecio(
        precio_venta=producto.precio_venta,
        precio_umbral=producto.precio_umbral,
        precio_bajo_umbral=producto.precio_bajo_umbral,
        precio_sobre_umbral=producto.precio_sobre_umbral,
        fracciones=tuple(
            FraccionPrecio(decimal=fr.decimal, precio_total=fr.precio_total)
            for fr in producto.fracciones
        ),
        # Sin esto el esquema queda en "Unidad" y el granel (gramo/cm/ml) NUNCA se aplica en
        # calcular_precio → el endpoint GET /productos/{id}/precio (que el POS consulta por línea)
        # cobraría la sub-unidad como precio_venta*cantidad. La señal granel la da unidad_medida.
        unidad_medida=producto.unidad_medida,
    )


@dataclass(frozen=True, slots=True)
class PrecioCalculado:
    producto_id: int
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal
    regla: str


@dataclass(frozen=True, slots=True)
class AjusteResultado:
    producto_id: int
    delta: Decimal
    stock_actual: Decimal
    replay: bool
    movimiento_id: int | None   # None en el conteo no-op (cantidad contada == stock; sin movimiento)


class InventarioService:
    def __init__(self, repo: SqlInventarioRepository) -> None:
        self._repo = repo

    async def buscar(self, query: str, *, limite: int = 10) -> ResultadoBusqueda:
        return await BuscadorProductos(self._repo).buscar(query, limite=limite)

    # ---- CRUD de catálogo (admin) -------------------------------------------
    async def crear_producto(self, datos: ProductoCrear, *, usuario_id: int | None) -> Producto:
        """Da de alta el producto (con fracciones; inventario en 0). 409 si el código ya existe; 422 si
        el `proveedor_id` no corresponde a un proveedor registrado."""
        if datos.codigo and await self._repo.codigo_existe(datos.codigo):
            raise CodigoDuplicado(datos.codigo)
        await self._validar_proveedor(datos.proveedor_id)
        return await self._repo.crear_producto(datos, usuario_id=usuario_id)

    async def actualizar_producto(
        self, producto_id: int, datos: ProductoActualizar
    ) -> Producto:
        """Edita el producto y reemplaza sus fracciones. Levanta ProductoInexistente / CodigoDuplicado /
        ProveedorInexistente (422)."""
        if datos.codigo and await self._repo.codigo_existe(datos.codigo, excluir_id=producto_id):
            raise CodigoDuplicado(datos.codigo)
        await self._validar_proveedor(datos.proveedor_id)
        producto = await self._repo.actualizar_producto(producto_id, datos)
        if producto is None:
            raise ProductoInexistente(producto_id)
        return producto

    async def _validar_proveedor(self, proveedor_id: int | None) -> None:
        """Si viene `proveedor_id`, exige que exista en `proveedores` (si no → ProveedorInexistente)."""
        if proveedor_id is not None and not await self._repo.proveedor_existe(proveedor_id):
            raise ProveedorInexistente(proveedor_id)

    async def eliminar_producto(self, producto_id: int) -> None:
        """Soft delete (activo=false). Levanta ProductoInexistente si no existe."""
        if not await self._repo.soft_delete_producto(producto_id):
            raise ProductoInexistente(producto_id)

    async def calcular_precio(self, producto_id: int, cantidad: Decimal) -> PrecioCalculado:
        producto = await self._repo.obtener_producto(producto_id)
        if producto is None:
            raise ProductoInexistente(producto_id)
        esquema = esquema_de(producto)
        total, precio_unitario = obtener_precio_para_cantidad(esquema, cantidad)
        return PrecioCalculado(
            producto_id=producto_id,
            cantidad=cantidad,
            precio_unitario=precio_unitario,
            total=total,
            regla=regla_para_cantidad(esquema, cantidad),
        )

    async def ajustar(
        self,
        *,
        producto_id: int,
        delta: Decimal,
        motivo: str,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> AjusteResultado:
        producto = await self._repo.obtener_producto(producto_id)
        if producto is None:
            raise ProductoInexistente(producto_id)

        # Lock primero: serializa los ajustes concurrentes del mismo producto. Así el chequeo de
        # idempotencia queda DENTRO de la sección crítica: un reintento concurrente con la misma
        # key espera el lock y luego ve el movimiento ya escrito → replay (no doble-apply ni 500).
        actual = await self._repo.lock_stock(producto_id)

        # Idempotencia estructural: si la key ya tiene movimiento, se devuelve ese (sin re-aplicar).
        if idempotency_key:
            previo = await self._repo.ajuste_por_key(idempotency_key)
            if previo is not None:
                stock = await self._repo.stock_actual(previo.producto_id)
                return AjusteResultado(
                    previo.producto_id, previo.cantidad, stock or Decimal("0"),
                    replay=True, movimiento_id=previo.id,
                )

        base = actual if actual is not None else Decimal("0")
        nuevo = base + delta
        if nuevo < 0:
            raise AjusteDejaStockNegativo(producto_id, base, delta)

        movimiento_id = await self._repo.aplicar_ajuste(
            producto_id=producto_id, delta=delta, nuevo_stock=nuevo,
            referencia=motivo, usuario_id=usuario_id, idempotency_key=idempotency_key,
        )
        return AjusteResultado(producto_id, delta, nuevo, replay=False, movimiento_id=movimiento_id)

    async def contar(
        self,
        *,
        producto_id: int,
        cantidad_contada: Decimal,
        motivo: str | None = None,
        usuario_id: int | None,
        idempotency_key: str | None = None,
    ) -> AjusteResultado:
        """Conteo físico (set-to-absolute): deja el stock en `cantidad_contada` (>= 0).

        Calcula `delta = cantidad_contada − stock_actual` (con la fila bloqueada) y REUSA el movimiento
        AJUSTE de `aplicar_ajuste` (no duplica la lógica de stock). Como `cantidad_contada >= 0`, el
        stock resultante nunca queda negativo: así se cuadran los negativos. Si `delta == 0`, es no-op
        (sin movimiento ni evento). Idempotente por `idempotency_key`. 404 si el producto no existe.
        """
        producto = await self._repo.obtener_producto(producto_id)
        if producto is None:
            raise ProductoInexistente(producto_id)

        # Lock antes de la idempotencia (igual que `ajustar`): serializa conteos concurrentes y deja el
        # chequeo de la key dentro de la sección crítica.
        actual = await self._repo.lock_stock(producto_id)

        if idempotency_key:
            previo = await self._repo.ajuste_por_key(idempotency_key)
            if previo is not None:
                stock = await self._repo.stock_actual(previo.producto_id)
                return AjusteResultado(
                    previo.producto_id, previo.cantidad, stock or Decimal("0"),
                    replay=True, movimiento_id=previo.id,
                )

        base = actual if actual is not None else Decimal("0")
        delta = cantidad_contada - base
        if delta == 0:
            # No-op de stock, pero el conteo CONFIRMA el físico: el sello de inventario progresivo
            # (`cuadrado_at`) se estampa igual — el producto pasa a "inventario confiable".
            await self._repo.sellar_cuadre(producto_id, fecha=now_co())
            return AjusteResultado(producto_id, Decimal("0"), base, replay=False, movimiento_id=None)

        # `nuevo` == cantidad_contada, pero con la escala del stock (NUMERIC 12,3) heredada de `base`.
        nuevo = base + delta
        movimiento_id = await self._repo.aplicar_ajuste(
            producto_id=producto_id, delta=delta, nuevo_stock=nuevo,
            referencia=motivo or "conteo físico", usuario_id=usuario_id, idempotency_key=idempotency_key,
        )
        # Inventario progresivo: todo conteo físico sella `cuadrado_at` (stock bajo / valor de
        # inventario solo confían en productos cuadrados). El AJUSTE relativo (`ajustar`) NO sella:
        # corregir un delta no equivale a haber contado el físico.
        await self._repo.sellar_cuadre(producto_id, fecha=now_co())
        return AjusteResultado(producto_id, delta, nuevo, replay=False, movimiento_id=movimiento_id)
