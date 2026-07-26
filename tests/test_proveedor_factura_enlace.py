"""La deuda queda ligada al PROVEEDOR, no a un nombre suelto (0070).

Sin este enlace, "cuánto le debo a cada proveedor" se agrupa por texto ("Ferrisariato" y
"FERRISARIATO SAS" son dos deudas del mismo señor) y la deuda no se puede cruzar con sus pedidos ni
con sus compras. Lo que se prueba: la factura que nace de una compra a crédito ya viene enlazada, el
alta manual resuelve el proveedor por nombre, y lo que no casa queda huérfano y se puede asignar.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from core.config.timezone import today_co
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import CompraCrear, CompraItemCrear, ProveedorRef
from modules.compras.service import ComprasService
from modules.proveedores.errors import FacturaProveedorInexistente
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import FacturaProveedorCrear
from modules.proveedores.service import ProveedoresService


async def _seed(s: AsyncSession):
    uid = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
        )
    ).scalar_one()
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion,"
                " activo) VALUES ('Martillo','unidad',11900,19,false,true) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,0,0)"),
        {"p": pid},
    )
    return uid, pid


async def test_la_compra_a_credito_deja_la_deuda_enlazada(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await _seed(s)
        await s.commit()

        compras = ComprasService(
            SqlComprasRepository(s), proveedores=SqlProveedoresRepository(s)
        )
        res = await compras.registrar(
            CompraCrear(
                proveedor=ProveedorRef(nombre="Ferrisariato"),
                items=[CompraItemCrear(producto_id=pid, cantidad=Decimal("1"), costo=Decimal("100"))],
                a_credito=True, numero_factura="F-ENL",
            ),
            usuario_id=uid,
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        proveedor_id = (
            await s.execute(
                text("SELECT proveedor_id FROM facturas_proveedores WHERE id='F-ENL'")
            )
        ).scalar_one()
    assert proveedor_id == res.compra.proveedor_id     # la deuda sabe a quién se le debe


async def test_alta_manual_resuelve_el_proveedor_por_nombre_y_lo_huerfano_se_asigna(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, _pid = await _seed(s)
        prov_id = (
            await s.execute(
                text("INSERT INTO proveedores (nombre) VALUES ('Ferrisariato') RETURNING id")
            )
        ).scalar_one()
        await s.commit()

        svc = ProveedoresService(SqlProveedoresRepository(s))
        # Mismo señor, escrito con otras mayúsculas y espacios: debe casar igual.
        casa = await svc.crear_factura(
            FacturaProveedorCrear(
                id="F-1", proveedor="  ferrisariato ", total=Decimal("1000"), fecha=today_co()
            ),
            usuario_id=uid,
        )
        # Un nombre que no existe: NO se inventa proveedor, queda huérfana.
        huerfana = await svc.crear_factura(
            FacturaProveedorCrear(
                id="F-2", proveedor="Distribuidora X", total=Decimal("2000"), fecha=today_co()
            ),
            usuario_id=uid,
        )
        await s.commit()

        assert casa.proveedor_id == prov_id
        assert huerfana.proveedor_id is None

        asignada = await svc.asignar_proveedor("F-2", prov_id)
        await s.commit()
        assert asignada.proveedor_id == prov_id

        with pytest.raises(FacturaProveedorInexistente):
            await svc.asignar_proveedor("NO-EXISTE", prov_id)
