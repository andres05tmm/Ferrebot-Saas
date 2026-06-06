"""Servicio de cuentas por pagar: validación de dominio sobre el repositorio (sin SQL).

Reglas: id de factura único (409); el abono debe existir la factura (404), ser > 0 y no exceder el
pendiente (422). La fecha por defecto es hoy en hora Colombia (regla #4). El recálculo del saldo lo
hace el repositorio en la misma transacción.
"""
from datetime import date

from core.config.timezone import today_co
from modules.proveedores.errors import (
    AbonoInvalido,
    FacturaProveedorDuplicada,
    FacturaProveedorInexistente,
)
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import (
    AbonoCrear,
    FacturaProveedorCrear,
    FacturaProveedorLeer,
    ProveedorLeer,
    ResumenCxP,
)


class ProveedoresService:
    def __init__(self, repo: SqlProveedoresRepository) -> None:
        self._repo = repo

    async def listar_proveedores(self) -> list[ProveedorLeer]:
        """Lista de proveedores registrados (id/nombre/nit) para los desplegables del modal."""
        return await self._repo.listar_proveedores()

    async def crear_factura(
        self, datos: FacturaProveedorCrear, *, usuario_id: int | None
    ) -> FacturaProveedorLeer:
        """Da de alta la deuda (pendiente=total). Si el id ya existe → FacturaProveedorDuplicada."""
        if await self._repo.existe(datos.id):
            raise FacturaProveedorDuplicada(datos.id)
        return await self._repo.crear_factura(
            factura_id=datos.id, proveedor=datos.proveedor, descripcion=datos.descripcion,
            total=datos.total, fecha=datos.fecha or today_co(), usuario_id=usuario_id,
        )

    async def registrar_abono(self, datos: AbonoCrear) -> FacturaProveedorLeer:
        """Registra el abono y devuelve la factura con el saldo recalculado.

        404 si la factura no existe; 422 si el monto excede el pendiente (criterio: no sobre-abonar).
        """
        factura = await self._repo.obtener(datos.factura_id)
        if factura is None:
            raise FacturaProveedorInexistente(datos.factura_id)
        if datos.monto > factura.pendiente:
            raise AbonoInvalido(
                f"El abono {datos.monto} excede el pendiente {factura.pendiente} de la factura {datos.factura_id!r}"
            )
        return await self._repo.crear_abono_y_recalcular(
            factura_id=datos.factura_id, monto=datos.monto, fecha=datos.fecha or today_co(),
        )

    async def listar(self, *, estado: str | None) -> list[FacturaProveedorLeer]:
        return await self._repo.listar(estado=estado)

    async def resumen(self) -> ResumenCxP:
        datos = await self._repo.resumen()
        return ResumenCxP(
            total_adeudado=datos.total_adeudado, facturas_pendientes=datos.facturas_pendientes
        )

    async def guardar_foto(
        self, factura_id: str, *, url: str, nombre: str | None
    ) -> FacturaProveedorLeer:
        """Persiste la URL de la foto subida a Cloudinary. 404 si la factura no existe."""
        factura = await self._repo.set_foto(factura_id, url=url, nombre=nombre)
        if factura is None:
            raise FacturaProveedorInexistente(factura_id)
        return factura
