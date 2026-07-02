"""Eval — aislamiento multi-tenant de las herramientas del agente (regla crítica, multitenancy.md).

A diferencia de `tests/test_tenant_isolation.py` (que prueba el servicio de ventas directo), aquí se
ejecuta una herramienta por el CAMINO REAL del agente —`Dispatcher.ejecutar` y `Bypass.intentar`,
con servicios y repositorios atados a la sesión del tenant— y se verifica que una operación de la
empresa A NO deja rastro en la base de la empresa B. El aislamiento lo da la base (DB-per-tenant):
cada empresa es un engine/sesión distinto; nunca se mezclan en un mismo flujo.

Usa las bases efímeras reales de `conftest.py` (Postgres migrado por test), porque el aislamiento es
una propiedad de la capa de datos, no algo que un fake pueda demostrar.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.bypass import Bypass
from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto, Resultado
from ai.ports import CatalogoDesdeVentas
from ai.tools import Deps
from apps.bot.catalogo import CatalogoBypassExacto
from core.llm.base import ToolCall
from modules.inventario.repository import SqlInventarioRepository
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService
from tests.evals._harness import NoConfig, NoKey, UmbralesFake

pytestmark = pytest.mark.eval


def _dispatcher() -> Dispatcher:
    return Dispatcher(config_store=NoConfig(), key_store=NoKey(), plataforma=None)


def _recursos(session: AsyncSession) -> Recursos:
    """Recursos con servicios/repos atados a ESTA sesión del tenant (sin cierre fiscal ni otros tenants)."""
    deps = Deps(
        ventas=VentaService(SqlVentasRepository(session)),
        caja=None, fiados=None, clientes=None,
    )
    return Recursos(
        deps=deps,
        catalogo=CatalogoDesdeVentas(SqlVentasRepository(session)),
        umbrales=UmbralesFake(confirmar=False),
    )


def _ctx(tenant_id: int, usuario_id: int) -> Contexto:
    return Contexto(
        tenant_id=tenant_id, usuario_id=usuario_id, rol="vendedor", origen="bot",
        idempotency_key=None, capacidades=frozenset({"ventas", "caja"}),
    )


async def _contar_ventas(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()


async def test_dispatcher_venta_de_A_no_toca_la_base_de_B(tenant_factory, seed_producto):
    """Una venta ejecutada por el despachador para la empresa A no aparece en la base de B."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    dispatcher = _dispatcher()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="50")
        tool_call = ToolCall(
            id="iso-disp", name="registrar_venta",
            arguments={
                "items": [{"producto_id": pid, "cantidad": Decimal("2")}],
                "metodo_pago": "efectivo",
            },
        )
        res = await dispatcher.ejecutar(tool_call, _ctx(1, uid), _recursos(s))
        await s.commit()

    assert isinstance(res, Resultado) and res.evento == "venta_registrada"
    assert await _contar_ventas(empresa_a.engine) == 1   # A registró su venta
    assert await _contar_ventas(empresa_b.engine) == 0   # B no ve NADA de A


async def test_bypass_venta_de_A_no_toca_la_base_de_B(tenant_factory, seed_producto):
    """El camino rápido (bypass) para la empresa A tampoco filtra hacia la base de B."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    dispatcher = _dispatcher()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, nombre="Martillo", stock="50")
        bypass = Bypass(
            CatalogoBypassExacto(SqlInventarioRepository(s), SqlVentasRepository(s)), dispatcher
        )
        res = await bypass.intentar("1 martillo", _ctx(1, uid), _recursos(s))
        await s.commit()

    assert isinstance(res, Resultado) and res.evento == "venta_registrada"
    assert await _contar_ventas(empresa_a.engine) == 1
    assert await _contar_ventas(empresa_b.engine) == 0
