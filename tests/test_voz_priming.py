"""Prompt-priming de Whisper con el vocabulario del catálogo (core/voz/priming.py).

- `formatear_prompt` es puro: arma el prompt con los términos, None si no hay ninguno.
- `prompt_para_tenant` lee los productos del tenant (top vendidos → activos), cachea 1h y no rompe
  si el catálogo está vacío.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.voz import priming


@pytest.fixture(autouse=True)
def _limpiar_cache():
    priming.limpiar_cache()
    yield
    priming.limpiar_cache()


def test_formatear_prompt_incluye_terminos():
    p = priming.formatear_prompt(["Wayper Pelado", "Varsol", "Drywall 6x1"])
    assert p is not None
    assert "Wayper Pelado" in p and "Varsol" in p and "Drywall 6x1" in p
    assert "ferretería" in p.lower()


def test_formatear_prompt_vacio_es_none():
    assert priming.formatear_prompt([]) is None
    assert priming.formatear_prompt(["", "   "]) is None


@pytest.mark.anyio
async def test_prompt_para_tenant_usa_activos_sin_ventas(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        await seed_producto(s, nombre="Wayper Pelado")
        p = await priming.prompt_para_tenant(s, tenant_id=1)
    assert p is not None and "Wayper Pelado" in p


@pytest.mark.anyio
async def test_prompt_para_tenant_prioriza_top_vendidos(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        u1, p1 = await seed_producto(s, nombre="Cemento Gris")
        _, p2 = await seed_producto(s, nombre="Puntilla 2")
        # Dos ventas de Cemento, una de Puntilla → Cemento debe aparecer antes en el vocabulario.
        v = (await s.execute(text(
            "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
            "metodo_pago, estado, origen) VALUES (1, :u, now(), 1000, 0, 1000, 'efectivo', "
            "'completada', 'bot') RETURNING id"), {"u": u1})).scalar_one()
        for pid in (p1, p1, p2):
            await s.execute(text(
                "INSERT INTO ventas_detalle (venta_id, producto_id, cantidad, precio_unitario, iva) "
                "VALUES (:v, :p, 1, 1000, 0)"), {"v": v, "p": pid})
        await s.commit()
        p = await priming.prompt_para_tenant(s, tenant_id=1)
    assert p is not None
    assert p.index("Cemento Gris") < p.index("Puntilla 2")


@pytest.mark.anyio
async def test_prompt_para_tenant_catalogo_vacio_es_none(tenant):
    async with AsyncSession(tenant.engine) as s:
        p = await priming.prompt_para_tenant(s, tenant_id=1)
    assert p is None
