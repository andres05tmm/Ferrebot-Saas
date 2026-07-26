"""Tipo de egreso, recurrentes y anulación del tab Gastos (migración 0071).

El invariante que se prueba aquí es económico, no técnico: **no todo lo que sale de la caja es
gasto**. Un retiro del dueño y la compra de una vitrina mueven plata pero NO restan en la utilidad —
si lo hicieran, el P&L mostraría pérdidas que no existen cada vez que el dueño saca plata o compra
una estantería. La caja, en cambio, tiene que contarlos todos: salieron de verdad.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de gastos)
from core.config.timezone import today_co
from modules.caja.errors import RecurrenteInexistente
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.service import ReportesService


def _caja(s: AsyncSession) -> CajaService:
    return CajaService(SqlCajaRepository(s))


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
        )
    ).scalar_one()


async def _egresos_ingresos(engine) -> tuple[Decimal, Decimal]:
    async with AsyncSession(engine) as s:
        filas = (
            await s.execute(
                text("SELECT tipo, coalesce(sum(monto),0) FROM caja_movimientos GROUP BY tipo")
            )
        ).all()
    por_tipo = {t: Decimal(m) for t, m in filas}
    return por_tipo.get("egreso", Decimal("0")), por_tipo.get("ingreso", Decimal("0"))


async def test_retiro_e_inversion_salen_de_caja_pero_no_del_pyl(tenant):
    """El invariante: los tres salen de la caja; solo el gasto resta en la utilidad."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        caja = _caja(s)
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await caja.registrar_gasto(
            usuario_id=uid, categoria="arriendo", monto=Decimal("700000"), concepto="local",
        )
        await caja.registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("200000"), concepto="para la casa",
            tipo_egreso="retiro",
        )
        await caja.registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("500000"), concepto="vitrina",
            tipo_egreso="inversion",
        )
        await s.commit()

    # La CAJA los cuenta todos: salió plata de verdad (1.400.000).
    egresos, _ = await _egresos_ingresos(tenant.engine)
    assert egresos == Decimal("1400000")

    async with AsyncSession(tenant.engine) as s:
        svc = ReportesService(SqlReportesRepository(s))
        pyl = await svc.estado_resultados(desde=None, hasta=None)
        resumen = await svc.resumen_gastos(desde=None, hasta=None)

    # El P&L solo resta el gasto: la utilidad no se come el retiro ni la vitrina.
    assert pyl.gastos == Decimal("700000")
    assert resumen.total_gasto == Decimal("700000")
    assert resumen.total_retiro == Decimal("200000")
    assert resumen.total_inversion == Decimal("500000")
    # Arriendo es FIJO → alimenta el punto de equilibrio; nada más lo hace.
    assert resumen.fijos == Decimal("700000")
    assert resumen.variables == Decimal("0")
    assert [r.categoria for r in resumen.por_categoria] == ["arriendo"]


async def test_anular_devuelve_la_plata_a_la_caja_y_saca_el_gasto_del_pyl(tenant):
    """Anular un gasto mal digitado: reversa CON movimiento (nunca delete) e idempotente."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        caja = _caja(s)
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        res = await caja.registrar_gasto(
            usuario_id=uid, categoria="empaque", monto=Decimal("80000"), concepto="bolsas",
        )
        await s.commit()
        gid = res.gasto.id

        anulado = await caja.rechazar_gasto(
            gid, usuario_id=uid, motivo="monto malo", exigir_pendiente=False
        )
        await s.commit()
        assert anulado.anulado_en is not None

        # Replay: re-anular no postea una segunda reversa.
        await caja.rechazar_gasto(gid, usuario_id=uid, exigir_pendiente=False)
        await s.commit()

    egresos, ingresos = await _egresos_ingresos(tenant.engine)
    assert (egresos, ingresos) == (Decimal("80000"), Decimal("80000"))   # se cancelan, UNA reversa

    async with AsyncSession(tenant.engine) as s:
        arqueo = await _caja(s).arqueo(1, modo_empresa=True)
        pyl = await ReportesService(SqlReportesRepository(s)).estado_resultados(
            desde=None, hasta=None
        )
        vivos = await SqlCajaRepository(s).listar_gastos()
    assert arqueo.saldo_esperado == Decimal("100000")
    assert pyl.gastos == Decimal("0")      # anulado: ya no cuenta como gasto en ninguna parte
    assert vivos == []


async def test_gasto_de_bandeja_sigue_exigiendo_estar_pendiente(tenant):
    """La anulación del dueño no abre la puerta de la bandeja: rechazar sigue siendo solo pendientes."""
    from modules.caja.errors import GastoNoPendiente

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        caja = _caja(s)
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        res = await caja.registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("10000"), concepto="manual",
        )
        await s.commit()
        with pytest.raises(GastoNoPendiente):
            await caja.rechazar_gasto(res.gasto.id, usuario_id=uid)


async def test_recurrente_se_da_por_pagado_por_el_vinculo(tenant):
    """El checklist del mes se resuelve por `recurrente_id`, nunca adivinando por nombre o monto."""
    from core.config.timezone import rango_dia_co

    hoy = today_co()
    inicio, fin = rango_dia_co(hoy.replace(day=1), hoy)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        caja = _caja(s)
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        rec = await caja.crear_recurrente(
            {"nombre": "Arriendo", "categoria": "arriendo",
             "monto_estimado": Decimal("700000"), "dia_mes": 5, "activo": True}
        )
        await s.commit()
        rec_id = rec.id

        pendientes = await caja.listar_recurrentes(inicio=inicio, fin=fin)
        assert [pago for _, pago in pendientes] == [None]

        # El monto real (720.000) manda sobre el estimado: la luz nunca llega igual dos meses.
        res = await caja.registrar_gasto(
            usuario_id=uid, categoria="arriendo", monto=Decimal("720000"),
            concepto="arriendo julio", recurrente_id=rec_id,
        )
        await s.commit()

        pagados = await caja.listar_recurrentes(inicio=inicio, fin=fin)
        (_, pago), = pagados
        assert pago is not None
        assert pago[0] == res.gasto.id
        assert pago[2] == Decimal("720000")

        # Un recurrente inexistente no revienta contra la FK: error de dominio → 404 en el router.
        with pytest.raises(RecurrenteInexistente):
            await caja.registrar_gasto(
                usuario_id=uid, categoria="otros", monto=Decimal("1000"), concepto=None,
                recurrente_id=999_999,
            )


async def test_punto_de_equilibrio_sale_de_los_fijos_y_el_margen(tenant, seed_producto):
    """Cuánto hay que vender para no perder = gastos FIJOS del mes ÷ margen bruto."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, nombre="Cemento")
        # Venta de 1.000.000 con costo 750.000 → margen bruto 25%.
        await s.execute(
            text(
                "INSERT INTO ventas (consecutivo, vendedor_id, subtotal, impuestos, total,"
                " metodo_pago, estado, fecha)"
                " VALUES (1, :u, 1000000, 0, 1000000, 'efectivo', 'completada', now())"
            ),
            {"u": uid},
        )
        await s.execute(
            text(
                "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad, costo_unitario,"
                " fecha_operacion) VALUES (:p, 'SALIDA', 1, 750000, now())"
            ),
            {"p": pid},
        )
        await s.commit()

        caja = _caja(s)
        await caja.abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await caja.registrar_gasto(
            usuario_id=uid, categoria="arriendo", monto=Decimal("500000"), concepto=None,
        )   # fijo
        await caja.registrar_gasto(
            usuario_id=uid, categoria="empaque", monto=Decimal("100000"), concepto=None,
        )   # variable: NO entra al equilibrio
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        resumen = await ReportesService(SqlReportesRepository(s)).resumen_gastos(
            desde=None, hasta=None
        )

    assert resumen.margen_bruto_pct == Decimal("25.00")
    assert resumen.punto_equilibrio_mes == Decimal("2000000.00")   # 500.000 ÷ 0,25
    assert resumen.pct_ventas == Decimal("60.00")                  # 600.000 de gasto / 1.000.000
