"""Integración del cotizador AIU contra Postgres efímero + aislamiento multi-tenant.

Cubre el CRUD real con ítems, la NUMERACIÓN consecutiva `PIM-0XX-AAAA` sin huecos ni colisiones, el
rechazo de un número duplicado (UNIQUE), el reemplazo de ítems del builder, la CONVERSIÓN idempotente
GANADA→Obra (invariante, contra la UNIQUE real de `obras.cotizacion_id`) y el aislamiento entre
empresas (empresa A jamás ve cotizaciones de B: la frontera es la base).
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.cotizacion_obra.errors import NumeroDuplicado
from modules.cotizacion_obra.repository import SqlCotizacionObraRepository
from modules.cotizacion_obra.schemas import CotizacionObraActualizar, CotizacionObraCrear, ItemCotizacionObraCrear
from modules.cotizacion_obra.service import CotizacionObraService
from modules.obra.repository import SqlObrasRepository
from modules.obra.service import ObrasService


async def _crear_cliente(session: AsyncSession, nombre: str = "Alcaldía") -> int:
    return (
        await session.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES (:n, 0) RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


def _servicio(session: AsyncSession) -> CotizacionObraService:
    return CotizacionObraService(
        SqlCotizacionObraRepository(session), ObrasService(SqlObrasRepository(session))
    )


def _datos(cliente_id: int, **kw) -> CotizacionObraCrear:
    base = dict(
        cliente_id=cliente_id,
        nombre_obra=kw.pop("nombre_obra", "Vía La Paz"),
        administracion_pct=Decimal("0.05"),
        imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"),
        iva_sobre_utilidad_pct=Decimal("0.19"),
        items=[
            ItemCotizacionObraCrear(
                orden=1, descripcion="Base granular", unidad="m3",
                cantidad=Decimal("1000"), valor_unitario=Decimal("10000"),
            )
        ],
    )
    base.update(kw)
    return CotizacionObraCrear(**base)


async def test_crud_items_y_totales_persisten(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        armada = await _servicio(s).crear(_datos(cid))
        await s.commit()
        cot_id = armada.cotizacion.id
        assert armada.cotizacion.numero.split("-")[:2] == ["PIM", "001"]   # primer consecutivo
        assert armada.cotizacion.estado == "BORRADOR"                       # default de la base
        assert armada.totales.total == Decimal("11276000.00")              # caso de aceptación del plan

    # relee en otra sesión: ítems y totales reconstruidos igual
    async with AsyncSession(tenant.engine) as s:
        got = await _servicio(s).obtener(cot_id)
        assert [(i.descripcion, i.cantidad) for i in got.items] == [("Base granular", Decimal("1000.0000"))]
        assert got.totales.total == Decimal("11276000.00")
        assert got.totales.iva_utilidad == Decimal("76000.00")   # IVA sólo sobre la utilidad


async def test_numeracion_consecutiva_sin_huecos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        svc = _servicio(s)
        numeros = []
        for _ in range(3):
            armada = await svc.crear(_datos(cid))
            await s.commit()
            numeros.append(armada.cotizacion.numero)

    medios = [int(n.split("-")[1]) for n in numeros]
    anios = {n.split("-")[2] for n in numeros}
    assert medios == [1, 2, 3]        # consecutivo sin huecos
    assert len(anios) == 1            # mismo año en toda la serie


async def test_numero_explicito_duplicado_rechaza(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        await _servicio(s).crear(_datos(cid, numero="PIM-777-2026"))
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(NumeroDuplicado):
            await _servicio(s).crear(_datos(cid, numero="PIM-777-2026", nombre_obra="Otra"))


async def test_actualizar_reemplaza_items(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        armada = await _servicio(s).crear(_datos(cid))
        await s.commit()
        cot_id = armada.cotizacion.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        nuevos = CotizacionObraActualizar(
            nombre_obra="Vía renombrada",
            items=[
                ItemCotizacionObraCrear(orden=1, descripcion="Imprimación", unidad="m2",
                                        cantidad=Decimal("500"), valor_unitario=Decimal("2000")),
                ItemCotizacionObraCrear(orden=2, descripcion="Carpeta asfáltica", unidad="m2",
                                        cantidad=Decimal("500"), valor_unitario=Decimal("8000")),
            ],
        )
        armada = await _servicio(s).actualizar(cot_id, nuevos)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        got = await _servicio(s).obtener(cot_id)
        assert got.cotizacion.nombre_obra == "Vía renombrada"
        assert [i.descripcion for i in got.items] == ["Imprimación", "Carpeta asfáltica"]  # viejo reemplazado
        # subtotal = 500*2000 + 500*8000 = 5.000.000
        assert got.totales.subtotal == Decimal("5000000.00")


async def test_conversion_ganada_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        svc = _servicio(s)
        armada = await svc.crear(_datos(cid))
        cot_id = armada.cotizacion.id
        await svc.cambiar_estado(cot_id, "ENVIADA")
        await svc.cambiar_estado(cot_id, "GANADA")
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        obra1 = await _servicio(s).convertir_a_obra(cot_id)
        await s.commit()
        oid1 = obra1.id
        assert obra1.cotizacion_id == cot_id and obra1.estado == "PLANIFICADA"

    # segunda conversión: MISMA obra, no una nueva (idempotencia contra la UNIQUE de cotizacion_id)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        obra2 = await _servicio(s).convertir_a_obra(cot_id)
        await s.commit()
        assert obra2.id == oid1

    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM obras WHERE cotizacion_id = :c"), {"c": cot_id}
            )
        ).scalar_one()
        assert n == 1   # una sola obra pese a las dos conversiones


async def test_crear_desde_cotizacion_carrera_devuelve_existente(tenant):
    """Carrera de conversión (MEDIUM-2): dos conversiones de la MISMA cotización GANADA pasan ambas el
    pre-check del servicio (obtener_por_cotizacion = None) y llaman al REPO; la perdedora choca contra
    la UNIQUE(cotizacion_id). El repo debe traducir esa colisión y devolver la obra EXISTENTE (misma
    id), no propagar un 500.

    Se fuerza el camino del IntegrityError de forma determinista: el 'ganador' inserta y COMMITEA la
    obra; el 'perdedor' llama directo a `crear_desde_cotizacion` (que no pre-chequea) → colisión."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s)
        svc = _servicio(s)
        armada = await svc.crear(_datos(cid))
        cot_id = armada.cotizacion.id
        await svc.cambiar_estado(cot_id, "ENVIADA")
        await svc.cambiar_estado(cot_id, "GANADA")
        await s.commit()

    # el "ganador" de la carrera inserta y committea la obra 1-1 de esa cotización
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cot = await SqlCotizacionObraRepository(s).obtener(cot_id)
        obra_ganadora = await SqlObrasRepository(s).crear_desde_cotizacion(cot)
        await s.commit()
        oid = obra_ganadora.id

    # el "perdedor" llega tarde y llama directo al repo → UNIQUE(cotizacion_id) choca en el flush
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cot = await SqlCotizacionObraRepository(s).obtener(cot_id)
        obra_perdedora = await SqlObrasRepository(s).crear_desde_cotizacion(cot)
        await s.commit()
        assert obra_perdedora.id == oid   # devuelve la existente, no una nueva ni una excepción

    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM obras WHERE cotizacion_id = :c"), {"c": cot_id}
            )
        ).scalar_one()
        assert n == 1   # una sola obra pese a la carrera


async def test_empresa_A_no_ve_cotizaciones_de_empresa_B(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        cid = await _crear_cliente(s, nombre="Cliente A")
        await _servicio(s).crear(_datos(cid, nombre_obra="Obra solo de A"))
        await s.commit()

    async with AsyncSession(empresa_a.engine) as s:
        assert len(await _servicio(s).listar()) == 1   # A ve su cotización

    async with AsyncSession(empresa_b.engine) as s:
        assert len(await _servicio(s).listar()) == 0   # B no ve nada de A
