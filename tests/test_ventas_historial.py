"""El libro de ventas (`historial_lineas`) contra una base efímera real.

Es el feed que el dueño mira a diario: una fila por RENGLÓN vendido. Lo que se prueba acá no es que
"devuelva datos" sino los cuatro sitios donde este tipo de consulta se rompe callada: el N+1, la
frontera del día colombiano, el aislamiento por vendedor, y los centavos de las fracciones.
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear, PagoParte
from modules.ventas.service import VentaService


def _venta(lineas, *, metodo="efectivo", cliente_id=None, pagos=None):
    return VentaCrear(
        metodo_pago=metodo, cliente_id=cliente_id, pagos=pagos or [],
        lineas=[VentaDetalleCrear(producto_id=p, cantidad=Decimal(c)) for p, c in lineas],
    )


async def _producto(s: AsyncSession, nombre: str, precio: str = "10000") -> int:
    pid = (
        await s.execute(
            text("INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                 "VALUES (:n,'unidad',:p,0,false,true) RETURNING id"),
            {"n": nombre, "p": precio},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:pid,1000,0)"),
        {"pid": pid},
    )
    return pid


async def _usuario(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n,'vendedor') RETURNING id"), {"n": nombre}
        )
    ).scalar_one()


async def test_el_feed_trae_el_renglon_con_su_contexto_resuelto(tenant):
    """Producto, cliente y vendedor salen resueltos del SQL: el front no debe adivinar ninguno."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('MARIA GOMEZ') RETURNING id"))
        ).scalar_one()
        p1 = await _producto(s, "Cemento gris 50kg", "20000")
        p2 = await _producto(s, "Varilla 1/2", "5000")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            _venta([(p1, "2"), (p2, "1")], cliente_id=cid), vendedor_id=uid
        )
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)

    assert [f.producto for f in feed.filas] == ["Cemento gris 50kg", "Varilla 1/2"]
    assert {f.cliente for f in feed.filas} == {"MARIA GOMEZ"}
    assert {f.vendedor for f in feed.filas} == {"Andrés"}
    assert [f.cantidad for f in feed.filas] == [Decimal("2.000"), Decimal("1.000")]
    assert [f.total_linea for f in feed.filas] == [Decimal("40000.00"), Decimal("5000.00")]
    # Las dos filas son de la MISMA venta: el front las agrupa por ahí para editar/anular la venta.
    assert len({f.venta_id for f in feed.filas}) == 1
    assert {f.num_lineas for f in feed.filas} == {2}
    assert feed.hay_mas is False


async def test_una_venta_sin_cliente_dice_consumidor_final(tenant):
    """Se resuelve en SQL, no en el front: si no, cada consumidor del feed inventa su propio texto."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Martillo")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta([(pid, "1")]), vendedor_id=uid)
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)

    assert feed.filas[0].cliente == "Consumidor Final"
    assert feed.filas[0].cliente_id is None


async def test_una_venta_mixta_trae_su_desglose_real(tenant):
    """`metodo_pago='mixto'` no es un método de pago, es un marcador: sin las partes, la columna
    Método del historial le mentiría al dueño sobre cómo le pagaron."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Pintura", "50000")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            _venta([(pid, "1")], metodo="mixto", pagos=[
                PagoParte(metodo="efectivo", monto=Decimal("30000")),
                PagoParte(metodo="transferencia", monto=Decimal("20000")),
            ]),
            vendedor_id=uid,
        )
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)

    fila = feed.filas[0]
    assert fila.metodo_pago == "mixto"
    assert [(p.metodo, p.monto) for p in fila.pagos] == [
        ("efectivo", Decimal("30000.00")), ("transferencia", Decimal("20000.00"))
    ]
    # El invariante del cobro mixto: las partes suman el total de la CABECERA.
    assert sum(p.monto for p in fila.pagos) == fila.venta_total


async def test_una_venta_normal_no_paga_la_consulta_de_mixtas(tenant):
    """La tercera consulta solo corre si hay mixtas. Sin esto, todo feed pagaría un SELECT de más."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Martillo")
        await s.commit()
        for _ in range(3):
            await VentaService(SqlVentasRepository(s)).registrar_venta(_venta([(pid, "1")]), vendedor_id=uid)
        await s.commit()

        consultas: list[str] = []

        def _contar(conn, cursor, stmt, params, ctx, many):    # noqa: ARG001
            if "ventas" in stmt.lower():
                consultas.append(stmt)

        event.listen(tenant.engine.sync_engine, "before_cursor_execute", _contar)
        try:
            hoy = today_co()
            feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)
        finally:
            event.remove(tenant.engine.sync_engine, "before_cursor_execute", _contar)

    assert len(feed.filas) == 3
    # DOS consultas: los ids de las ventas y sus renglones en batch. Ni una por venta (N+1).
    assert len(consultas) == 2, f"esperaba 2 consultas, hubo {len(consultas)}"


async def test_la_pagina_nunca_parte_una_venta_por_la_mitad(tenant):
    """`limite` cuenta VENTAS, no renglones. Paginar por renglón dejaría medio grupo en cada página
    y el front tendría que reconstruir grupos a caballo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        p1 = await _producto(s, "A")
        p2 = await _producto(s, "B")
        p3 = await _producto(s, "C")
        await s.commit()
        svc = VentaService(SqlVentasRepository(s))
        await svc.registrar_venta(_venta([(p1, "1"), (p2, "1"), (p3, "1")]), vendedor_id=uid)
        await svc.registrar_venta(_venta([(p1, "1"), (p2, "1")]), vendedor_id=uid)
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy, limite=1)

    # Una sola venta (la más reciente, por el orden fecha DESC) y COMPLETA: todos sus renglones.
    # Se asserta contra `num_lineas` y no contra un 3 fijo: el invariante es "el grupo no se parte",
    # no "cae esta venta". Fijar la venta ataría el test al orden en vez de a la regla.
    assert len({f.venta_id for f in feed.filas}) == 1
    assert len(feed.filas) == feed.filas[0].num_lineas
    assert feed.hay_mas is True          # queda la otra venta, sin pagar un COUNT(*) del rango


async def test_un_vendedor_no_ve_los_renglones_de_otro(tenant):
    """Aislamiento por rol contra la BASE, no contra un fake: es plata de otra persona."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ana = await _usuario(s, "Ana")
        beto = await _usuario(s, "Beto")
        pid = await _producto(s, "Martillo")
        await s.commit()
        svc = VentaService(SqlVentasRepository(s))
        await svc.registrar_venta(_venta([(pid, "1")]), vendedor_id=ana)
        await svc.registrar_venta(_venta([(pid, "2")]), vendedor_id=beto)
        await s.commit()

        hoy = today_co()
        repo = SqlVentasRepository(s)
        de_ana = await repo.historial_lineas(desde=hoy, hasta=hoy, vendedor_id=ana)
        todas = await repo.historial_lineas(desde=hoy, hasta=hoy)

    assert [f.vendedor for f in de_ana.filas] == ["Ana"]
    assert len(todas.filas) == 2          # el admin (sin filtro) sí las ve todas


async def test_una_venta_de_la_noche_no_se_va_al_dia_siguiente(tenant):
    """La frontera del día colombiano. Con `::date` crudo, una venta de las 7 p.m. cae en el día
    siguiente según el TimeZone de la sesión de Postgres, y desaparece del historial de su día."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await s.execute(text("SET TIME ZONE 'UTC'"))     # el peor caso: sesión en UTC
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Martillo")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta([(pid, "1")]), vendedor_id=uid)
        hoy = today_co()
        # 23:30 hora Colombia de hoy = 04:30 UTC de mañana.
        # CAST y no `:d::date`: en un `text()` de SQLAlchemy esa sintaxis rompe el bind param.
        await s.execute(
            text("UPDATE ventas SET fecha = (CAST(:d AS date) + time '23:30') "
                 "AT TIME ZONE 'America/Bogota'"),
            {"d": hoy},
        )
        await s.commit()

        repo = SqlVentasRepository(s)
        de_hoy = await repo.historial_lineas(desde=hoy, hasta=hoy)
        de_manana = await repo.historial_lineas(desde=hoy + timedelta(days=1), hasta=hoy + timedelta(days=1))

    assert len(de_hoy.filas) == 1
    assert de_manana.filas == []


async def test_una_venta_anulada_sigue_en_el_libro_marcada(tenant):
    """Es un libro: lo que se anuló también es historia. El KPI sí las excluye — esa asimetría es a
    propósito, y por eso el estado viaja en cada fila para que el front pueda atenuarlas."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Martillo")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta([(pid, "1")]), vendedor_id=uid)
        await s.execute(text("UPDATE ventas SET estado='anulada'"))
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)

    assert [f.estado for f in feed.filas] == ["anulada"]


async def test_el_total_de_la_venta_manda_sobre_la_suma_de_los_renglones(tenant):
    """El invariante nº 1 del tab.

    Para fracciones y empaques el servicio guarda `precio_unitario = total / cantidad`
    (`service.py:492`), así que `Σ(cantidad × precio_unitario)` puede no dar el total de la venta.
    Cada fila lleva `venta_total` justamente para que ninguna cifra que se sume salga de las líneas:
    si el Excel sumara los renglones, contradiría al KPI por centavos.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s, "Andrés")
        pid = await _producto(s, "Cable", "1000")
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(_venta([(pid, "3")]), vendedor_id=uid)
        # Se fuerza el caso de la fracción: un unitario con más decimales de los que caben.
        await s.execute(text("UPDATE ventas_detalle SET precio_unitario = 333.33"))
        await s.execute(text("UPDATE ventas SET total = 1000.00"))
        await s.commit()

        hoy = today_co()
        feed = await SqlVentasRepository(s).historial_lineas(desde=hoy, hasta=hoy)

    fila = feed.filas[0]
    assert fila.total_linea == Decimal("999.99")      # 3 x 333.33, como lo reconstruye la DIAN
    assert fila.venta_total == Decimal("1000.00")     # lo que de verdad se cobró
    assert fila.total_linea != fila.venta_total       # el centavo de diferencia, hecho explícito
