"""Gasto real de una obra: suma exacta de los 5 componentes + semáforo por umbral + alerta de margen.

El diferenciador del vertical (plan PIM §4). Se corre contra Postgres efímero (fixture `tenant`) sembrando
los componentes reales (gastos, compras, prorrateo de nómina, horas de máquina costeadas y consumos de
inventario) y la cotización GANADA de la que sale el presupuesto (ingreso = subtotal+A+I+U, sin IVA;
utilidad = U). Verifica que el total agregado sea EXACTO y que el semáforo/alerta caigan por umbral.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.obra.repository import SqlObrasRepository
from modules.obra.service import ObrasService

# Todas las columnas param_* de periodos_nomina son NOT NULL sin default: se siembran en 0 (sólo hace
# falta el periodo como FK del prorrateo; los valores legales no intervienen en el gasto real).
_PARAMS_NOMINA = (
    "param_smmlv", "param_auxilio_transporte", "param_auxilio_transporte_tope_smmlv",
    "param_horas_mes", "param_recargo_he_diurna", "param_recargo_he_nocturna", "param_recargo_dominical",
    "param_salud_empleado_pct", "param_pension_empleado_pct", "param_salud_empleador_pct",
    "param_pension_empleador_pct", "param_arl_pct", "param_caja_compensacion_pct", "param_sena_pct",
    "param_icbf_pct", "param_cesantias_pct", "param_intereses_cesantias_pct", "param_prima_pct",
    "param_vacaciones_pct",
)


def _servicio(s: AsyncSession) -> ObrasService:
    return ObrasService(SqlObrasRepository(s))   # gasto_real no mueve inventario: no necesita el port


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id")
        )
    ).scalar_one()


async def _obra_con_cotizacion(
    s: AsyncSession,
    cid: int,
    *,
    items: list[tuple[str, str]],
    a="0.05",
    i="0.03",
    u="0.04",
    estado_obra="EN_EJECUCION",
) -> int:
    """Crea cotización GANADA (con ítems) + la obra 1-1 ligada, y devuelve el obra_id."""
    numero = f"PIM-{uuid.uuid4().hex[:8]}-2026"
    cot_id = (
        await s.execute(
            text(
                "INSERT INTO cotizaciones_obra "
                "(numero, cliente_id, nombre_obra, administracion_pct, imprevistos_pct, utilidad_pct, "
                " iva_sobre_utilidad_pct, estado) "
                "VALUES (:num,:c,'Vía',:a,:i,:u,0.19,'GANADA') RETURNING id"
            ),
            {"num": numero, "c": cid, "a": a, "i": i, "u": u},
        )
    ).scalar_one()
    for orden, (cant, vu) in enumerate(items, start=1):
        await s.execute(
            text(
                "INSERT INTO items_cotizacion_obra "
                "(cotizacion_id, orden, descripcion, unidad, cantidad, valor_unitario) "
                "VALUES (:c,:o,'renglón','m3',:cant,:vu)"
            ),
            {"c": cot_id, "o": orden, "cant": cant, "vu": vu},
        )
    return (
        await s.execute(
            text(
                "INSERT INTO obras (cotizacion_id, cliente_id, nombre, estado) "
                "VALUES (:cot,:c,'Obra',:e) RETURNING id"
            ),
            {"cot": cot_id, "c": cid, "e": estado_obra},
        )
    ).scalar_one()


async def _gasto(s: AsyncSession, oid: int, monto: str) -> None:
    await s.execute(
        text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', :m, :o)"),
        {"m": monto, "o": oid},
    )


async def _compra(s: AsyncSession, oid: int, total: str) -> None:
    await s.execute(
        text("INSERT INTO compras (total, obra_id) VALUES (:t, :o)"), {"t": total, "o": oid}
    )


async def _consumo(s: AsyncSession, oid: int, cantidad: str, costo: str) -> None:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Arena','m3','1',0,false,true) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO consumos_inventario (producto_id, obra_id, fecha, cantidad, costo_unitario) "
            "VALUES (:p,:o,'2026-07-01',:cant,:costo)"
        ),
        {"p": pid, "o": oid, "cant": cantidad, "costo": costo},
    )


async def _horas(s: AsyncSession, oid: int, horas_fact: str, costo_op: str) -> None:
    mid = (
        await s.execute(
            text(
                "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default, costo_operacion_hora) "
                "VALUES (:cod,'Vibro','compactador','150000',:co) RETURNING id"
            ),
            {"cod": f"M-{uuid.uuid4().hex[:6]}", "co": costo_op},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO registros_horas_maquina "
            "(maquina_id, obra_id, fecha, horas_trabajadas, horas_facturables) "
            "VALUES (:m,:o,'2026-07-01',:h,:h)"
        ),
        {"m": mid, "o": oid, "h": horas_fact},
    )


async def _prorrateo(s: AsyncSession, oid: int, costo: str) -> None:
    """Siembra periodo + trabajador (FKs) y una fila de prorrateo imputada a la obra."""
    # tope_smmlv es Integer; el resto son Numeric → Decimal. Todos en 0 (no intervienen en el gasto real).
    cols = {
        "fecha_inicio": date(2026, 7, 1), "fecha_fin": date(2026, 7, 15),
        **{
            p: (0 if p == "param_auxilio_transporte_tope_smmlv" else Decimal("0"))
            for p in _PARAMS_NOMINA
        },
    }
    nombres = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    periodo_id = (
        await s.execute(
            text(f"INSERT INTO periodos_nomina ({nombres}) VALUES ({binds}) RETURNING id"), cols
        )
    ).scalar_one()
    trab_id = (
        await s.execute(
            text(
                "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo) "
                "VALUES ('DIRECTO', :doc, 'Pedro', 'Pérez', 'Operador') RETURNING id"
            ),
            {"doc": uuid.uuid4().hex[:12]},
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO prorrateo_nomina_obra "
            "(periodo_id, trabajador_id, obra_id, dias_imputados, costo_imputado) "
            "VALUES (:per,:t,:o,10,:c)"
        ),
        {"per": periodo_id, "t": trab_id, "o": oid, "c": costo},
    )


async def test_gasto_real_suma_exacta_de_los_cinco_componentes(tenant):
    """subtotal 10M → ingreso 11.2M / U 400k. Componentes conocidos suman EXACTO; margen amplio → verde."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, items=[("1000", "10000")])   # subtotal = 10.000.000
        await _gasto(s, oid, "1000000")            # gastos      = 1.000.000
        await _compra(s, oid, "2000000")           # compras     = 2.000.000
        await _prorrateo(s, oid, "500000")         # prorrateo   =   500.000
        await _horas(s, oid, "10", "50000")        # horas 10×50k=   500.000
        await _consumo(s, oid, "100", "1000")      # consumos 100×1k=100.000
        await s.commit()

        r = await _servicio(s).gasto_real(oid)

    d = r.desglose
    assert d.total_gastos == Decimal("1000000.00")
    assert d.total_compras == Decimal("2000000.00")
    assert d.total_prorrateo_nomina == Decimal("500000.00")
    assert d.total_horas_maquina == Decimal("500000.00")
    assert d.total_consumos_inventario == Decimal("100000.00")
    assert d.total == Decimal("4100000.00")                     # suma exacta de los 5
    # presupuesto de la cotización GANADA (IVA fuera del ingreso)
    assert r.ingreso_presupuestado == Decimal("11200000.00")
    assert r.utilidad_presupuestada == Decimal("400000.00")
    assert r.tiene_presupuesto is True
    assert r.utilidad_real == Decimal("7100000.00")             # 11.2M − 4.1M
    assert d.semaforo.value == "verde"                          # margen ≫ utilidad presupuestada
    assert r.alerta_margen is False


# (gasto único que empuja el total, semáforo esperado, alerta esperada). Presupuesto: ingreso 11.2M, U 400k.
#   margen = 11.200.000 − total ; verde ≥ 400k ; amarillo 0–400k ; rojo < 0 ; alerta si margen < 200k.
_CASOS = [
    ("1000000", "verde", False),      # margen 10.2M
    ("11000000", "amarillo", False),  # margen 200.000 (== 50% de U: NO alerta, estricto <)
    ("11100000", "amarillo", True),   # margen 100.000 (< 200k → alerta)
    ("11300000", "rojo", True),       # margen −100.000 (pérdida)
]


@pytest.mark.parametrize("gasto,semaforo,alerta", _CASOS)
async def test_semaforo_y_alerta_por_umbral(tenant, gasto: str, semaforo: str, alerta: bool):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = await _obra_con_cotizacion(s, cid, items=[("1000", "10000")])
        await _gasto(s, oid, gasto)
        await s.commit()
        r = await _servicio(s).gasto_real(oid)

    assert r.desglose.semaforo.value == semaforo
    assert r.alerta_margen is alerta


async def test_obra_sin_cotizacion_no_tiene_presupuesto(tenant):
    """Obra suelta (sin cotización): sin presupuesto no hay contra qué medir → rojo, sin alerta."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre) VALUES (:c,'Suelta') RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        await _gasto(s, oid, "500000")
        await s.commit()
        r = await _servicio(s).gasto_real(oid)

    assert r.tiene_presupuesto is False
    assert r.ingreso_presupuestado == Decimal("0.00")
    assert r.desglose.total == Decimal("500000.00")
    assert r.utilidad_real == Decimal("-500000.00")
    assert r.desglose.semaforo.value == "rojo"
    assert r.alerta_margen is False   # sin presupuesto no se alerta (no hay umbral)


async def test_gasto_real_aislado_entre_empresas(tenant_factory):
    """El gasto real agrega SOLO dentro de la base del tenant: B no ve los componentes de A."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    async with AsyncSession(empresa_a.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid_a = await _obra_con_cotizacion(s, cid, items=[("1000", "10000")])
        await _gasto(s, oid_a, "3000000")
        await s.commit()

    async with AsyncSession(empresa_b.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        oid_b = await _obra_con_cotizacion(s, cid, items=[("1000", "10000")])
        await s.commit()
        r_b = await _servicio(s).gasto_real(oid_b)

    assert r_b.desglose.total == Decimal("0.00")   # la obra de B no arrastra el gasto de A
