"""Extensión mini-CRM del vertical construcción sobre clientes y proveedores (Fase 1).

Las columnas nuevas (tenant 0046) se exponen en los schemas/endpoints EXISTENTES como campos
OPCIONALES: sin ellas, el contrato del POS/retail no cambia (backward-compatible). Cubre:
  - schemas: `ClienteCrear` acepta el mini-CRM opcional y valida el literal de `estatus`; `ClienteLeer`
    cae al default None cuando el objeto no trae el atributo (como el SimpleNamespace del router).
  - repositorio (base efímera real): `crear` persiste el mini-CRM; sin `estatus` aplica el
    server_default 'PROSPECTO' (0046), no NULL.
  - proveedores: `ProveedorLeer` devuelve `tipo`/`contacto_*` (y null cuando el proveedor no los trae).
"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.clientes.repository import SqlClientesRepository
from modules.clientes.schemas import ClienteCrear, ClienteLeer
from modules.clientes.service import ClientesService
from modules.proveedores.repository import SqlProveedoresRepository


# --- Schemas (sin BD) -------------------------------------------------------
def test_cliente_crear_acepta_mini_crm_opcional():
    c = ClienteCrear(
        nombre="Constructora XYZ", estatus="ACTIVO", contacto_nombre="Ing. Ana",
        contacto_cargo="Compras", contacto_telefono="3001112233",
        contacto_email="ana@xyz.co", acuerdo_comercial="30 días, 5% pronto pago",
    )
    assert c.estatus == "ACTIVO"
    assert c.contacto_nombre == "Ing. Ana"
    assert c.acuerdo_comercial == "30 días, 5% pronto pago"


def test_cliente_crear_backward_compatible_sin_mini_crm():
    """El alta mínima del POS (solo nombre) sigue válida: los campos nuevos caen a None."""
    c = ClienteCrear(nombre="Cliente mostrador")
    assert c.estatus is None
    assert (c.contacto_nombre, c.contacto_email, c.acuerdo_comercial) == (None, None, None)


def test_cliente_crear_rechaza_estatus_fuera_del_enum():
    with pytest.raises(ValidationError):
        ClienteCrear(nombre="X", estatus="INEXISTENTE")


def test_cliente_leer_default_none_cuando_objeto_no_trae_atributo():
    """`from_attributes` cae al default None si el objeto no expone el atributo — así el fake
    SimpleNamespace del test de router (sin las columnas nuevas) sigue serializando sin romper."""
    obj = SimpleNamespace(
        id=1, nombre="Ana", tipo_documento="CC", documento="1", telefono=None, correo=None,
        direccion=None, ciudad_dane=None, regimen=None, saldo_fiado=Decimal("0"),
        creado_en=datetime(2026, 1, 1, 12, 0, 0),
    )
    leido = ClienteLeer.model_validate(obj)
    assert leido.estatus is None
    assert leido.contacto_nombre is None and leido.acuerdo_comercial is None


# --- Repositorio de clientes (base efímera real) ----------------------------
async def test_crear_persiste_mini_crm_construccion(tenant):
    datos = ClienteCrear(
        nombre="Constructora XYZ", tipo_documento="NIT", documento="900999001",
        estatus="RECURRENTE", contacto_nombre="Ing. Ana", contacto_cargo="Compras",
        contacto_telefono="3001112233", contacto_email="ana@xyz.co",
        acuerdo_comercial="30 días",
    )
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await ClientesService(SqlClientesRepository(s)).crear(datos)
        await s.commit()
        c = res.cliente

    leido = ClienteLeer.model_validate(c)
    assert leido.estatus == "RECURRENTE"
    assert leido.contacto_nombre == "Ing. Ana"
    assert leido.contacto_cargo == "Compras"
    assert leido.contacto_telefono == "3001112233"
    assert leido.contacto_email == "ana@xyz.co"
    assert leido.acuerdo_comercial == "30 días"

    # Y quedó persistido (no solo en el objeto en memoria).
    async with AsyncSession(tenant.engine) as s:
        fila = (
            await s.execute(
                text("SELECT estatus, contacto_email FROM clientes WHERE documento = '900999001'")
            )
        ).one()
    assert fila.estatus == "RECURRENTE" and fila.contacto_email == "ana@xyz.co"


async def test_crear_sin_estatus_aplica_server_default_prospecto(tenant):
    """Sin `estatus`, la columna toma el server_default 'PROSPECTO' de la 0046 (no NULL): el repositorio
    OMITE la columna del INSERT y el flush la puebla vía RETURNING."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await ClientesService(SqlClientesRepository(s)).crear(
            ClienteCrear(nombre="Cliente mostrador", documento="12345")
        )
        await s.commit()
        c = res.cliente

    assert c.estatus == "PROSPECTO"                 # poblado por el server_default, no None
    assert c.contacto_nombre is None

    async with AsyncSession(tenant.engine) as s:
        estatus = (
            await s.execute(text("SELECT estatus FROM clientes WHERE documento = '12345'"))
        ).scalar_one()
    assert estatus == "PROSPECTO"


# --- Repositorio de proveedores (base efímera real) -------------------------
async def test_listar_proveedores_incluye_mini_crm_construccion(tenant):
    async with AsyncSession(tenant.engine) as s:
        await s.execute(
            text(
                "INSERT INTO proveedores (nombre, nit, tipo, contacto_nombre, contacto_telefono, "
                "contacto_email) VALUES "
                "('Cantera La Roca', '901.1', 'CANTERA_ARENA', 'Don Pedro', '3009998877', 'pedro@roca.co'), "
                "('Ferre Retail', '900.9', NULL, NULL, NULL, NULL)"
            )
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        provs = {p.nombre: p for p in await SqlProveedoresRepository(s).listar_proveedores()}

    roca = provs["Cantera La Roca"]
    assert roca.tipo == "CANTERA_ARENA"
    assert roca.contacto_nombre == "Don Pedro"
    assert roca.contacto_telefono == "3009998877"
    assert roca.contacto_email == "pedro@roca.co"

    # Backward-compatible: un proveedor del POS sin mini-CRM devuelve los campos como null.
    retail = provs["Ferre Retail"]
    assert retail.tipo is None
    assert (retail.contacto_nombre, retail.contacto_telefono, retail.contacto_email) == (None, None, None)
