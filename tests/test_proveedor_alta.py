"""Alta y edición de proveedores desde el tab.

Hasta ahora un proveedor solo nacía de rebote al registrar una compra (`get_or_create` por nombre),
así que una ferretería recién montada abría el tab y no veía a nadie, ni tenía dónde anotar el
teléfono. Lo que se prueba: el alta guarda los datos de contacto, el nombre repetido se rechaza
(dos fichas del mismo proveedor partirían su deuda en dos) y la edición corrige sin duplicar.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.proveedores.errors import ProveedorDuplicado, ProveedorInexistente
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import ProveedorGuardar
from modules.proveedores.service import ProveedoresService


async def test_alta_guarda_los_datos_de_contacto_y_aparece_en_la_lista(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = ProveedoresService(SqlProveedoresRepository(s))
        creado = await svc.crear_proveedor(ProveedorGuardar(
            nombre="  Ferrisariato  ", nit="900123456-7", telefono="3001234567",
            contacto_nombre="Doña Marta", correo="",
        ))
        await s.commit()

        assert creado.nombre == "Ferrisariato"        # el nombre se guarda limpio
        assert creado.telefono == "3001234567"
        assert creado.contacto_nombre == "Doña Marta"
        assert creado.correo is None                  # campo en blanco = "no sé", no cadena vacía

        assert [p.nombre for p in await svc.listar_proveedores()] == ["Ferrisariato"]


async def test_nombre_repetido_se_rechaza(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = ProveedoresService(SqlProveedoresRepository(s))
        await svc.crear_proveedor(ProveedorGuardar(nombre="Ferrisariato"))
        await s.commit()

        # Mismas letras con otras mayúsculas y espacios: es el mismo señor.
        with pytest.raises(ProveedorDuplicado):
            await svc.crear_proveedor(ProveedorGuardar(nombre=" ferrisariato "))


async def test_editar_corrige_los_datos_y_no_choca_consigo_mismo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = ProveedoresService(SqlProveedoresRepository(s))
        prov = await svc.crear_proveedor(ProveedorGuardar(nombre="Ferrisariato"))
        otro = await svc.crear_proveedor(ProveedorGuardar(nombre="Distribuidora X"))
        await s.commit()

        # Guardar sin renombrar: el chequeo de duplicado no puede confundirlo con él mismo.
        actualizado = await svc.actualizar_proveedor(
            prov.id, ProveedorGuardar(nombre="Ferrisariato", telefono="3005555555")
        )
        await s.commit()
        assert actualizado.telefono == "3005555555"

        # Renombrarlo al nombre de otro sí choca.
        with pytest.raises(ProveedorDuplicado):
            await svc.actualizar_proveedor(prov.id, ProveedorGuardar(nombre=otro.nombre))

        with pytest.raises(ProveedorInexistente):
            await svc.actualizar_proveedor(999999, ProveedorGuardar(nombre="Nadie"))
