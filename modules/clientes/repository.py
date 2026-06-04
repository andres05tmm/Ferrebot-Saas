"""Repositorio de clientes: único lugar con SQL del módulo (regla no negociable #2).

Alta mínima del cliente. `saldo_fiado` arranca en 0 (la fuente de verdad del crédito es
`fiados_movimientos`, no esta columna). La dedup por documento la decide el servicio; aquí solo
se hace la búsqueda y el insert.
"""
from decimal import Decimal

from sqlalchemy import select
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
