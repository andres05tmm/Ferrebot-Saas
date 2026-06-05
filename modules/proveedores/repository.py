"""Repositorio de cuentas por pagar: único lugar con SQL del módulo (regla no negociable #2).

`pagado`/`pendiente`/`estado` de una factura son DERIVADOS de sus abonos: al registrar un abono se
recalculan (pagado = Σ abonos; pendiente = total − pagado, con clamp a 0; estado = 'pagada' si
pendiente ≤ 0). Decimal en dinero; la sesión del tenant es la transacción.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.money import cuantizar
from modules.proveedores.models import AbonoProveedor, FacturaProveedor
from modules.proveedores.schemas import FacturaProveedorLeer


@dataclass(frozen=True, slots=True)
class ResumenDatos:
    total_adeudado: Decimal
    facturas_pendientes: int


class SqlProveedoresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def existe(self, factura_id: str) -> bool:
        return (
            await self._s.execute(
                select(FacturaProveedor.id).where(FacturaProveedor.id == factura_id).limit(1)
            )
        ).first() is not None

    async def crear_factura(
        self, *, factura_id: str, proveedor: str, descripcion: str | None,
        total: Decimal, fecha: date, usuario_id: int | None,
    ) -> FacturaProveedorLeer:
        """INSERT con pagado=0, pendiente=total, estado='pendiente' (montos cuantizados a centavos)."""
        total = cuantizar(total)
        orm = FacturaProveedor(
            id=factura_id, proveedor=proveedor, descripcion=descripcion, total=total,
            pagado=Decimal("0.00"), pendiente=total, estado="pendiente", fecha=fecha, usuario_id=usuario_id,
        )
        self._s.add(orm)
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)

    async def obtener(self, factura_id: str) -> FacturaProveedorLeer | None:
        orm = (
            await self._s.execute(select(FacturaProveedor).where(FacturaProveedor.id == factura_id))
        ).scalar_one_or_none()
        return FacturaProveedorLeer.model_validate(orm) if orm is not None else None

    async def crear_abono_y_recalcular(
        self, *, factura_id: str, monto: Decimal, fecha: date
    ) -> FacturaProveedorLeer:
        """Inserta el abono y recalcula pagado/pendiente/estado de la factura (en la misma tx)."""
        orm = (
            await self._s.execute(
                select(FacturaProveedor).where(FacturaProveedor.id == factura_id).with_for_update()
            )
        ).scalar_one()
        self._s.add(AbonoProveedor(factura_id=factura_id, monto=monto, fecha=fecha))
        await self._s.flush()

        pagado = (
            await self._s.execute(
                select(func.coalesce(func.sum(AbonoProveedor.monto), 0)).where(
                    AbonoProveedor.factura_id == factura_id
                )
            )
        ).scalar_one()
        pagado = cuantizar(Decimal(pagado))
        pendiente = cuantizar(orm.total - pagado)
        if pendiente < 0:
            pendiente = Decimal("0.00")
        orm.pagado = pagado
        orm.pendiente = pendiente
        orm.estado = "pagada" if pendiente <= 0 else "pendiente"
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)

    async def listar(self, *, estado: str | None = None) -> list[FacturaProveedorLeer]:
        stmt = select(FacturaProveedor)
        if estado is not None:
            stmt = stmt.where(FacturaProveedor.estado == estado)
        stmt = stmt.order_by(FacturaProveedor.fecha.desc(), FacturaProveedor.id.desc())
        filas = (await self._s.execute(stmt)).scalars().all()
        return [FacturaProveedorLeer.model_validate(f) for f in filas]

    async def resumen(self) -> ResumenDatos:
        """Total pendiente (estado != 'pagada') y número de facturas pendientes."""
        total, n = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(FacturaProveedor.pendiente), 0),
                    func.count(),
                ).where(FacturaProveedor.estado != "pagada")
            )
        ).one()
        return ResumenDatos(total_adeudado=cuantizar(Decimal(total)), facturas_pendientes=int(n))

    async def set_foto(
        self, factura_id: str, *, url: str, nombre: str | None
    ) -> FacturaProveedorLeer | None:
        orm = (
            await self._s.execute(select(FacturaProveedor).where(FacturaProveedor.id == factura_id))
        ).scalar_one_or_none()
        if orm is None:
            return None
        orm.foto_url = url
        orm.foto_nombre = nombre
        await self._s.flush()
        return FacturaProveedorLeer.model_validate(orm)
