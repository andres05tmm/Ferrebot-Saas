"""COGS por promedio ponderado móvil (ADR 0025, Fase 2 Contable A).

TDD de invariantes críticos:
- El promedio ponderado es correcto tras varias compras (fórmula pura + integración).
- Bajo compras CONCURRENTES del mismo producto no hay lost update (gracias al FOR UPDATE del producto).
- Reprocesar la MISMA compra (idempotency_key) no re-promedia (idempotencia).
- La SALIDA snapshotea `costo_promedio` en `costo_unitario` sin romper "nada mueve stock sin
  movimiento de inventario".
"""
import asyncio
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.compras.repository import SqlComprasRepository, _promedio_ponderado
from modules.compras.schemas import CompraCrear
from modules.compras.service import ComprasService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear
from modules.ventas.service import VentaService


# ---- Fórmula pura ----------------------------------------------------------
def test_promedio_ponderado_arranca_en_el_costo_si_no_hay_previo():
    # stock 0, promedio NULL → arranca en el costo de la compra.
    assert _promedio_ponderado(Decimal("0"), None, Decimal("10"), Decimal("100")) == Decimal("100.00")


def test_promedio_ponderado_pondera_por_stock():
    # (10·100 + 10·200) / 20 = 150.
    assert _promedio_ponderado(Decimal("10"), Decimal("100"), Decimal("10"), Decimal("200")) == Decimal("150.00")


def test_promedio_ponderado_stock_negativo_cuenta_como_cero():
    # Stock en rojo (modo permisivo) no aporta valor: el promedio se rehace desde el costo nuevo.
    assert _promedio_ponderado(Decimal("-5"), Decimal("100"), Decimal("10"), Decimal("200")) == Decimal("200.00")


def test_promedio_ponderado_cuantiza_a_centavos():
    # (1·100 + 2·101) / 3 = 100.6666... → 100.67.
    assert _promedio_ponderado(Decimal("1"), Decimal("100"), Decimal("2"), Decimal("101")) == Decimal("100.67")


# ---- Helpers de siembra ----------------------------------------------------
async def _seed_producto(s: AsyncSession, *, stock: str = "0", precio_compra: str | None = None) -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, iva, "
                "permite_fraccion, activo) VALUES ('Cemento','unidad',20000,:pc,19,false,true) RETURNING id"
            ),
            {"pc": precio_compra},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Q','admin') RETURNING id"))
    ).scalar_one()


async def _compra(session: AsyncSession, pid: int, cantidad, costo, *, key: str | None = None):
    datos = CompraCrear(
        proveedor={"nombre": "Prov"},
        items=[{"producto_id": pid, "cantidad": cantidad, "costo": costo}],
        idempotency_key=key,
    )
    return await ComprasService(SqlComprasRepository(session)).registrar(datos, usuario_id=None)


async def _promedio(engine, pid: int) -> Decimal | None:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT costo_promedio FROM productos WHERE id=:p"), {"p": pid})
        ).scalar_one()


# ---- Integración: recálculo por compra -------------------------------------
async def test_compra_recalcula_promedio_ponderado_movil(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _seed_producto(s, stock="0")
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _compra(s, pid, "10", "100")   # stock 0 → promedio 100
        await s.commit()
    assert await _promedio(tenant.engine, pid) == Decimal("100.00")

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _compra(s, pid, "10", "200")   # (10·100 + 10·200)/20 = 150
        await s.commit()
    assert await _promedio(tenant.engine, pid) == Decimal("150.00")

    async with AsyncSession(tenant.engine) as s:
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("20.000")


# ---- Invariante: nada mueve stock sin movimiento + snapshot del promedio ----
async def test_venta_snapshotea_costo_promedio_en_la_salida(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _seed_producto(s, stock="0")
        await s.commit()

    # Dos compras dejan el promedio en 150.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _compra(s, pid, "10", "100")
        await _compra(s, pid, "10", "200")
        await s.commit()

    # Vender 3 unidades: el SALIDA debe existir (invariante) y su costo = promedio (150), no el último 200.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        datos = VentaCrear(metodo_pago="efectivo", origen="web", lineas=[{"producto_id": pid, "cantidad": "3"}])
        await VentaService(SqlVentasRepository(s)).registrar_venta(datos, vendedor_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        tipo, cant, costo = (
            await s.execute(
                text("SELECT tipo, cantidad, costo_unitario FROM movimientos_inventario "
                     "WHERE producto_id=:p AND tipo='SALIDA'"),
                {"p": pid},
            )
        ).one()
        assert tipo == "SALIDA" and cant == Decimal("3.000")
        assert costo == Decimal("150.00")   # promedio ponderado, NO el último precio_compra (200)


# ---- Idempotencia: el replay no re-promedia --------------------------------
async def test_replay_de_compra_no_repromedia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _seed_producto(s, stock="0")
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _compra(s, pid, "10", "100", key="k-1")
        await s.commit()
    assert r1.replay is False
    assert await _promedio(tenant.engine, pid) == Decimal("100.00")

    # Misma key + mismo payload → replay: NO debe volver a promediar (seguiría en 100, no en 66.67).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _compra(s, pid, "10", "100", key="k-1")
        await s.commit()
    assert r2.replay is True
    assert await _promedio(tenant.engine, pid) == Decimal("100.00")

    async with AsyncSession(tenant.engine) as s:
        n_mov = (await s.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE producto_id=:p AND tipo='ENTRADA'"),
            {"p": pid},
        )).scalar_one()
        assert n_mov == 1   # una sola ENTRADA (no se duplicó el movimiento)


# ---- Invariante crítico: concurrencia sin lost update ----------------------
async def test_compras_concurrentes_no_pierden_actualizacion_del_promedio(tenant):
    """Dos compras simultáneas del mismo producto: el FOR UPDATE del producto las serializa.

    Sin el lock, ambas leerían stock 0 / promedio NULL y la última escritura ganaría (promedio = 100
    ó 200, y stock perdido). Con el lock, el resultado es el promedio ponderado correcto (150) y el
    stock sumado (20), independientemente del orden."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _seed_producto(s, stock="0")
        await s.commit()

    async def compra(costo: str) -> None:
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            await _compra(s, pid, "10", costo)
            await s.commit()

    await asyncio.gather(compra("100"), compra("200"))

    assert await _promedio(tenant.engine, pid) == Decimal("150.00")
    async with AsyncSession(tenant.engine) as s:
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("20.000")   # 10 + 10: ninguna ENTRADA se perdió
