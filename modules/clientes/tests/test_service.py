"""Servicio de clientes: dedup por documento (regla del tag crear_cliente, ai-tools.md §5.4).

Unitario con repo falso: el servicio no debe duplicar un cliente que ya existe por documento,
y debe crear cuando no hay documento (sin clave natural). Sin BD.
"""
from decimal import Decimal

from modules.clientes.models import Cliente
from modules.clientes.schemas import ClienteCrear
from modules.clientes.service import ClientesService, ResultadoCliente


class FakeClientesRepo:
    def __init__(self, existentes: list[Cliente] | None = None) -> None:
        self._por_doc = {c.documento: c for c in (existentes or []) if c.documento}
        self.creados: list[ClienteCrear] = []
        self._next_id = 100

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
