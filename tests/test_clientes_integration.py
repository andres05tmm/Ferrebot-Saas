"""Integración del repositorio de clientes contra una base efímera real (Postgres).

Verifica que el alta persiste y que la dedup por documento (decidida en el servicio) no
inserta una segunda fila cuando el documento ya existe.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.clientes.repository import SqlClientesRepository
from modules.clientes.schemas import ClienteCrear
from modules.clientes.service import ClientesService


async def test_alta_persiste_y_dedup_por_documento(tenant):
    datos = ClienteCrear(nombre="Ferretería La 80", tipo_documento="NIT", documento="900123456")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await ClientesService(SqlClientesRepository(s)).crear(datos)
        await s.commit()
        assert r1.creado is True
        cid = r1.cliente.id

    # Segundo alta con el mismo documento → dedup, devuelve el existente, no inserta.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await ClientesService(SqlClientesRepository(s)).crear(
            ClienteCrear(nombre="La 80 SAS", tipo_documento="NIT", documento="900123456")
        )
        await s.commit()
        assert r2.creado is False
        assert r2.cliente.id == cid

    async with AsyncSession(tenant.engine) as s:
        total = (
            await s.execute(text("SELECT count(*) FROM clientes WHERE documento = '900123456'"))
        ).scalar_one()
        assert total == 1


async def test_obtener_y_listar_con_filtro_ilike(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlClientesRepository(s)
        ana = await repo.crear(ClienteCrear(nombre="Ana Pérez", tipo_documento="CC", documento="111"))
        await repo.crear(ClienteCrear(nombre="Carlos Gómez", tipo_documento="CC", documento="222"))
        await s.commit()
        ana_id = ana.id

    async with AsyncSession(tenant.engine) as s:
        repo = SqlClientesRepository(s)
        encontrado = await repo.obtener(ana_id)
        assert encontrado is not None and encontrado.nombre == "Ana Pérez"
        assert await repo.obtener(999_999) is None
        # listar() ordena por nombre
        assert [c.nombre for c in await repo.listar()] == ["Ana Pérez", "Carlos Gómez"]
        # q filtra por nombre (case-insensitive) y por documento
        assert [c.documento for c in await repo.listar("ana")] == ["111"]
        assert [c.nombre for c in await repo.listar("222")] == ["Carlos Gómez"]
