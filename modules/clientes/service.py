"""Servicio de clientes: alta mínima con dedup por documento (ai-tools.md §5.4).

Regla portada del tag `crear_cliente` de FerreBot: si ya existe un cliente con ese `documento`
se devuelve el existente con `creado=False` (no se duplica ni se actualiza). Sin `documento`
no hay clave natural para deduplicar → siempre crea. SQL solo en el repositorio (regla #2);
el servicio depende del puerto `ClientesRepo`, falseado en los tests unitarios.
"""
from dataclasses import dataclass
from typing import Protocol

from modules.clientes.models import Cliente
from modules.clientes.schemas import ClienteActualizar, ClienteCrear


@dataclass(frozen=True, slots=True)
class ResultadoCliente:
    cliente: Cliente
    creado: bool  # False si ya existía por documento (dedup)


class ClientesRepo(Protocol):
    """Puerto de datos de clientes (lo implementa SqlClientesRepository; los tests lo falsean)."""

    async def buscar_por_documento(self, documento: str) -> Cliente | None: ...
    async def crear(self, datos: ClienteCrear) -> Cliente: ...
    async def obtener(self, cliente_id: int) -> Cliente | None: ...
    async def listar(self, q: str | None = None) -> list[Cliente]: ...
    async def actualizar(self, cliente: Cliente, datos: ClienteActualizar) -> Cliente: ...
    async def eliminar(self, cliente: Cliente) -> None: ...


class ClientesService:
    def __init__(self, repo: ClientesRepo) -> None:
        self._repo = repo

    async def crear(self, datos: ClienteCrear) -> ResultadoCliente:
        if datos.documento:
            existente = await self._repo.buscar_por_documento(datos.documento)
            if existente is not None:
                return ResultadoCliente(cliente=existente, creado=False)
        cliente = await self._repo.crear(datos)
        return ResultadoCliente(cliente=cliente, creado=True)

    async def obtener(self, cliente_id: int) -> Cliente | None:
        return await self._repo.obtener(cliente_id)

    async def listar(self, q: str | None = None) -> list[Cliente]:
        return await self._repo.listar(q)

    async def actualizar(self, cliente_id: int, datos: ClienteActualizar) -> Cliente | None:
        cliente = await self._repo.obtener(cliente_id)
        if cliente is None:
            return None
        return await self._repo.actualizar(cliente, datos)

    async def eliminar(self, cliente_id: int) -> bool:
        """True si lo borró; False si no existía. Si tiene ventas, la FK revienta y el error sube."""
        cliente = await self._repo.obtener(cliente_id)
        if cliente is None:
            return False
        await self._repo.eliminar(cliente)
        return True
