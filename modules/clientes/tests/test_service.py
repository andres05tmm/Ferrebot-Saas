"""Servicio de clientes: dedup por documento (regla del tag crear_cliente, ai-tools.md §5.4).

Unitario con repo falso: el servicio no debe duplicar un cliente que ya existe por documento,
y debe crear cuando no hay documento (sin clave natural). Sin BD.
"""
from decimal import Decimal

from modules.clientes.models import Cliente
from modules.clientes.schemas import ClienteActualizar, ClienteCrear
from modules.clientes.service import ClientesService, ResultadoCliente


class FakeClientesRepo:
    def __init__(self, existentes: list[Cliente] | None = None) -> None:
        self._por_doc = {c.documento: c for c in (existentes or []) if c.documento}
        self._por_id = {c.id: c for c in (existentes or [])}
        self.creados: list[ClienteCrear] = []
        self.borrados: list[int] = []
        self._next_id = 100

    async def obtener(self, cliente_id: int) -> Cliente | None:
        return self._por_id.get(cliente_id)

    async def actualizar(self, cliente: Cliente, datos: ClienteActualizar) -> Cliente:
        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(cliente, campo, valor)
        return cliente

    async def eliminar(self, cliente: Cliente) -> None:
        self.borrados.append(cliente.id)
        self._por_id.pop(cliente.id, None)

    async def buscar_por_documento(self, documento: str) -> Cliente | None:
        return self._por_doc.get(documento)

    async def crear(self, datos: ClienteCrear) -> Cliente:
        self.creados.append(datos)
        self._next_id += 1
        return Cliente(
            id=self._next_id, nombre=datos.nombre, tipo_documento=datos.tipo_documento,
            documento=datos.documento, telefono=datos.telefono, correo=datos.correo,
            direccion=datos.direccion, ciudad_dane=datos.ciudad_dane, regimen=datos.regimen,
            saldo_fiado=Decimal("0"),
        )


async def test_crea_cuando_no_existe_por_documento():
    repo = FakeClientesRepo()
    res = await ClientesService(repo).crear(
        ClienteCrear(nombre="Juan Pérez", tipo_documento="CC", documento="1088")
    )
    assert isinstance(res, ResultadoCliente)
    assert res.creado is True
    assert res.cliente.documento == "1088"
    assert len(repo.creados) == 1


async def test_dedup_devuelve_existente_sin_crear():
    previo = Cliente(id=7, nombre="Juan", documento="1088", saldo_fiado=Decimal("5000"))
    repo = FakeClientesRepo([previo])
    res = await ClientesService(repo).crear(
        ClienteCrear(nombre="Juan Pérez", tipo_documento="CC", documento="1088")
    )
    assert res.creado is False
    assert res.cliente.id == 7              # el existente, intacto
    assert res.cliente.saldo_fiado == Decimal("5000")
    assert repo.creados == []               # no se duplicó


async def test_sin_documento_siempre_crea():
    repo = FakeClientesRepo()
    r1 = await ClientesService(repo).crear(ClienteCrear(nombre="Cliente mostrador"))
    r2 = await ClientesService(repo).crear(ClienteCrear(nombre="Cliente mostrador"))
    assert r1.creado is True and r2.creado is True
    assert len(repo.creados) == 2           # sin clave natural → no deduplica


async def test_actualizar_solo_toca_los_campos_enviados():
    """Patch parcial: editar el teléfono no puede borrar los datos fiscales que el form no mandó."""
    previo = Cliente(
        id=7, nombre="Juan", documento="1088", telefono="300", ciudad_dane="13001",
        regimen="responsable_iva", saldo_fiado=Decimal("0"),
    )
    repo = FakeClientesRepo([previo])
    actualizado = await ClientesService(repo).actualizar(7, ClienteActualizar(telefono="311"))

    assert actualizado is not None
    assert actualizado.telefono == "311"
    assert actualizado.ciudad_dane == "13001"      # intacto
    assert actualizado.regimen == "responsable_iva"


async def test_actualizar_cliente_inexistente_devuelve_none():
    assert await ClientesService(FakeClientesRepo()).actualizar(9, ClienteActualizar(nombre="X")) is None


async def test_eliminar_devuelve_si_existia():
    previo = Cliente(id=7, nombre="Juan", documento="1088", saldo_fiado=Decimal("0"))
    repo = FakeClientesRepo([previo])
    assert await ClientesService(repo).eliminar(7) is True
    assert repo.borrados == [7]
    assert await ClientesService(repo).eliminar(7) is False    # ya no está
