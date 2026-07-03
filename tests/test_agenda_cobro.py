"""Puente cita → venta (ADR 0022): cobrar una cita crea la venta, idempotente y sin tocar stock/caja.

Invariantes críticos (test-primero): idempotencia del cobro (doble cobro NO duplica), "nada mueve
stock sin movimiento" (la línea varia no descuenta) y "nada mueve caja sin registro" (el arqueo
híbrido cuadra por `ventas_efectivo`, SIN fila en `caja_movimientos`).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.agenda.cobro import CitaNoCobrable, cobrar_cita
from modules.agenda.errors import CitaInexistente
from modules.agenda.repository import SqlAgendaRepository
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService


async def _seed_cita(s: AsyncSession, *, precio="25000", estado="pendiente") -> tuple[int, int]:
    """(usuario_id, cita_id): vendedor + servicio con precio + recurso + cita en `estado`."""
    usuario_id = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Estilista','vendedor') RETURNING id")
        )
    ).scalar_one()
    servicio_id = (
        await s.execute(
            text(
                "INSERT INTO servicios (nombre, duracion_min, precio) "
                "VALUES ('Corte', 30, :p) RETURNING id"
            ),
            {"p": precio},
        )
    ).scalar_one()
    recurso_id = (
        await s.execute(
            text("INSERT INTO recursos (nombre, tipo) VALUES ('Silla 1','profesional') RETURNING id")
        )
    ).scalar_one()
    cita_id = (
        await s.execute(
            text(
                "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, "
                "inicio, fin, estado) VALUES (:sv, :rc, 'Ana', '+573001112233', "
                "now(), now() + interval '30 minutes', :est) RETURNING id"
            ),
            {"sv": servicio_id, "rc": recurso_id, "est": estado},
        )
    ).scalar_one()
    await s.commit()
    return usuario_id, cita_id


def _armar(s: AsyncSession):
    """(repo agenda, servicio de ventas) sobre la MISMA sesión (una sola transacción)."""
    return SqlAgendaRepository(s), VentaService(SqlVentasRepository(s))


async def _cobrar(s, cita_id, usuario_id, **kw):
    repo, ventas = _armar(s)
    res = await cobrar_cita(
        cita_id, repo=repo, ventas=ventas, usuario_id=usuario_id,
        metodo_pago=kw.pop("metodo_pago", "efectivo"), **kw,
    )
    await s.commit()
    return res


async def test_cobrar_crea_venta_varia_y_marca_cumplida(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)
        res = await _cobrar(s, cita_id, usuario_id)

        assert res.replay is False and res.total == Decimal("25000.00")
        fila = (
            await s.execute(
                text(
                    "SELECT v.total, v.metodo_pago, v.idempotency_key, c.estado, c.venta_id, "
                    "c.cobrada_en FROM ventas v JOIN citas c ON c.venta_id = v.id WHERE c.id = :c"
                ),
                {"c": cita_id},
            )
        ).one()
        assert fila.total == Decimal("25000.00") and fila.metodo_pago == "efectivo"
        assert fila.idempotency_key == f"cita-cobro:{cita_id}"
        assert fila.estado == "cumplida" and fila.venta_id == res.venta_id
        assert fila.cobrada_en is not None
        # La línea es VARIA: sin producto_id y con la descripción del servicio.
        linea = (
            await s.execute(
                text("SELECT producto_id, descripcion FROM ventas_detalle WHERE venta_id = :v"),
                {"v": res.venta_id},
            )
        ).one()
        assert linea.producto_id is None and "Corte" in linea.descripcion


async def test_doble_cobro_es_replay_y_no_duplica(tenant):
    # INVARIANTE idempotencia: reintentar el cobro devuelve la MISMA venta, sin duplicar.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)
        primero = await _cobrar(s, cita_id, usuario_id)
        segundo = await _cobrar(s, cita_id, usuario_id)

        assert segundo.replay is True and segundo.venta_id == primero.venta_id
        n = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        assert n == 1


async def test_cobro_no_toca_stock_ni_caja_movimientos(tenant):
    # INVARIANTES stock/caja: la línea varia no genera movimiento de inventario y el arqueo híbrido
    # cuadra por ventas_efectivo (insertar caja_movimientos doble-contaría — guardrail de arqueo.py).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)
        await _cobrar(s, cita_id, usuario_id)

        movs_inv = (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()
        movs_caja = (await s.execute(text("SELECT count(*) FROM caja_movimientos"))).scalar_one()
        assert movs_inv == 0 and movs_caja == 0


async def test_arqueo_del_dia_cuadra_con_el_cobro_en_efectivo(tenant):
    # E2E de caja: abrir con 10.000, cobrar 25.000 en efectivo, cerrar contando 35.000 → diferencia 0.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)
        caja = CajaService(SqlCajaRepository(s))
        await caja.abrir(usuario_id=usuario_id, saldo_inicial=Decimal("10000"))
        await s.commit()

        await _cobrar(s, cita_id, usuario_id)

        cierre = await caja.cerrar(usuario_id=usuario_id, saldo_contado=Decimal("35000"))
        await s.commit()
        assert cierre.saldo_esperado == Decimal("35000.00")
        assert cierre.diferencia == Decimal("0.00")


async def test_cita_cancelada_no_se_cobra(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s, estado="cancelada")
        repo, ventas = _armar(s)
        with pytest.raises(CitaNoCobrable):
            await cobrar_cita(cita_id, repo=repo, ventas=ventas, usuario_id=usuario_id,
                              metodo_pago="efectivo")


async def test_cita_inexistente_404(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _ = await _seed_cita(s)
        repo, ventas = _armar(s)
        with pytest.raises(CitaInexistente):
            await cobrar_cita(999_999, repo=repo, ventas=ventas, usuario_id=usuario_id,
                              metodo_pago="efectivo")


async def test_servicio_sin_precio_exige_override(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s, precio=None)
        repo, ventas = _armar(s)
        with pytest.raises(CitaNoCobrable):
            await cobrar_cita(cita_id, repo=repo, ventas=ventas, usuario_id=usuario_id,
                              metodo_pago="efectivo")
        # Con override (p. ej. reserva por noches, ADR 0022 §D6) sí cobra.
        res = await cobrar_cita(cita_id, repo=repo, ventas=ventas, usuario_id=usuario_id,
                                metodo_pago="efectivo", precio_override=Decimal("120000"))
        await s.commit()
        assert res.total == Decimal("120000.00")


async def test_endpoint_cobrar_gateado_por_ventas(tenant):
    # El endpoint exige pack_agenda (router) + ventas (ruta): sin `ventas` responde 404.
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.agenda.router import router as agenda_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def _app(caps: frozenset[str]) -> FastAPI:
        app = FastAPI()
        app.include_router(agenda_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol="vendedor")
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)

    sin_ventas = _app(frozenset({"pack_agenda"}))
    transport = ASGITransport(app=sin_ventas, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/api/v1/agenda/citas/{cita_id}/cobrar", json={"metodo_pago": "efectivo"})
    assert r.status_code == 404, r.text

    con_ventas = _app(frozenset({"pack_agenda", "ventas", "caja"}))
    transport = ASGITransport(app=con_ventas, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/api/v1/agenda/citas/{cita_id}/cobrar", json={"metodo_pago": "efectivo"})
    assert r.status_code == 201, r.text
    cuerpo = r.json()
    assert cuerpo["replay"] is False and Decimal(str(cuerpo["total"])) == Decimal("25000.0")


async def test_recuperacion_venta_creada_sin_vincular(tenant):
    # Crash entre crear la venta y vincularla (o carrera perdida): el reintento VINCULA la venta
    # existente por su idempotency_key en vez de crear otra.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, cita_id = await _seed_cita(s)
        repo, ventas = _armar(s)
        primera = await cobrar_cita(cita_id, repo=repo, ventas=ventas, usuario_id=usuario_id,
                                    metodo_pago="efectivo")
        # Simular el crash: des-vincular la cita (la venta queda huérfana con su key).
        await s.execute(
            text("UPDATE citas SET venta_id = NULL, estado = 'confirmada' WHERE id = :c"),
            {"c": cita_id},
        )
        await s.commit()

        res = await _cobrar(s, cita_id, usuario_id)
        assert res.replay is True and res.venta_id == primera.venta_id
        n = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        assert n == 1


async def test_cobro_anticipado_no_libera_el_slot(tenant):
    """Cobrar una cita FUTURA no la marca 'cumplida': `cumplida` es terminal y deja de ocupar
    agenda — el slot pagado quedaría libre para otra reserva. El cobro queda en venta_id/cobrada_en
    y la cita sigue activa (ocupando su intervalo) hasta que ocurra."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _ = await _seed_cita(s)   # reusa vendedor/servicio/recurso del seed
        servicio_id = (await s.execute(text("SELECT id FROM servicios LIMIT 1"))).scalar_one()
        recurso_id = (await s.execute(text("SELECT id FROM recursos LIMIT 1"))).scalar_one()
        futura_id = (
            await s.execute(
                text(
                    "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, "
                    "inicio, fin, estado) VALUES (:sv, :rc, 'Luis', '+573009998877', "
                    "now() + interval '2 days', now() + interval '2 days 30 minutes', 'confirmada') "
                    "RETURNING id"
                ),
                {"sv": servicio_id, "rc": recurso_id},
            )
        ).scalar_one()
        await s.commit()

        res = await _cobrar(s, futura_id, usuario_id)
        fila = (
            await s.execute(
                text("SELECT estado, venta_id, cobrada_en FROM citas WHERE id = :c"), {"c": futura_id}
            )
        ).one()
    assert res.replay is False
    assert fila.venta_id == res.venta_id and fila.cobrada_en is not None
    assert fila.estado == "confirmada"   # sigue ACTIVA: ocupa agenda y el anti-no-show la ve
