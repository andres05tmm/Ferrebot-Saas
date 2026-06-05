"""Repositorio de clientes: único lugar con SQL del módulo (regla no negociable #2).

Alta mínima del cliente. `saldo_fiado` arranca en 0 (la fuente de verdad del crédito es
`fiados_movimientos`, no esta columna). La dedup por documento la decide el servicio; aquí solo
se hace la búsqueda y el insert.
"""
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.clientes.models import Cliente
from modules.clientes.schemas import ClienteCrear


class SqlClientesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def buscar_por_documento(self, documento: str) -> Cliente | None:
        return (
            await self._s.execute(select(Cliente).where(Cliente.documento == documento))
        ).scalar_one_or_none()

    async def obtener(self, cliente_id: int) -> Cliente | None:
        return (
            await self._s.execute(select(Cliente).where(Cliente.id == cliente_id))
        ).scalar_one_or_none()

    async def listar(self, q: str | None = None) -> list[Cliente]:
        """Clientes ordenados por nombre; si `q`, filtra por nombre o documento (ILIKE)."""
        stmt = select(Cliente)
        if q:
            patron = f"%{q}%"
            stmt = stmt.where(or_(Cliente.nombre.ilike(patron), Cliente.documento.ilike(patron)))
        stmt = stmt.order_by(Cliente.nombre)
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear(self, datos: ClienteCrear) -> Cliente:
        cliente = Cliente(
            nombre=datos.nombre,
            tipo_documento=datos.tipo_documento,
            documento=datos.documento,
            telefono=datos.telefono,
            correo=datos.correo,
            direccion=datos.direccion,
            ciudad_dane=datos.ciudad_dane,
            regimen=datos.regimen,
            saldo_fiado=Decimal("0"),
        )
        self._s.add(cliente)
        await self._s.flush()  # asigna cliente.id
        return cliente
