"""Siembra transaccional de demos (plan §4-§5): datos vivos con fechas relativas + reset idempotente.

Integración contra BD efímera: primero se siembra el CATÁLOGO (servicios/recursos/productos) con los
loaders de manifiesto, luego `resembrar_demo` siembra lo transaccional. Se afirma: fechas relativas a
`ahora`, idempotencia (mismo `ahora` → mismos conteos), reset (otro `ahora` borra lo viejo) y el
despacho por capacidad (reservas tiene prioridad sobre citas en un hotel).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from core.config.timezone import COLOMBIA_TZ
from core.db.urls import to_libpq
from tools.manifest.packs.agenda import cargar_agenda
from tools.manifest.packs.pedidos import cargar_pedidos
from tools.manifest.packs.pos import cargar_pos
from tools.manifest.schema import (
    AgendaConfig,
    Disponibilidad,
    PackAgenda,
    PackPedidos,
    PackPos,
    PedidoConfig,
    ProductoPos,
    Recurso,
    Servicio,
    ZonaDomicilio,
)
from tools.seed_demo_transaccional import resembrar_demo

# Fechas fijas para asertar relatividad determinista (martes y, una semana después, otro martes).
_AHORA = datetime(2026, 6, 9, 13, 0, tzinfo=COLOMBIA_TZ)
_AHORA_SIG = datetime(2026, 6, 16, 13, 0, tzinfo=COLOMBIA_TZ)


def _conteo(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


def _agenda_barberia() -> PackAgenda:
    disp = [Disponibilidad(dias=[0, 1, 2, 3, 4, 5], franjas=["09:00-13:00", "14:00-19:00"])]
    return PackAgenda(
        config=AgendaConfig(persona="barbero"),
        servicios=[
            Servicio(nombre="Corte", duracion_min=30, precio=25000),
            Servicio(nombre="Barba", duracion_min=20, precio=18000),
        ],
        recursos=[
            Recurso(nombre="Andrés", tipo="profesional", presta=["Corte", "Barba"], disponibilidad=disp),
            Recurso(nombre="Carlos", tipo="profesional", presta=["Corte"], disponibilidad=disp),
        ],
    )


def _agenda_hotel() -> PackAgenda:
    return PackAgenda(
        config=AgendaConfig(checkin_hora="15:00", checkout_hora="12:00", persona="hotel"),
        servicios=[Servicio(nombre="Noche Estándar", duracion_min=1440, precio=180000)],
        recursos=[
            Recurso(nombre="Hab 101", tipo="habitacion", presta=["Noche Estándar"]),
            Recurso(nombre="Hab 102", tipo="habitacion", presta=["Noche Estándar"]),
        ],
    )


async def test_citas_relativas_idempotentes_y_reset(tenant):
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_agenda(_agenda_barberia(), conn)
        conn.commit()

    conteos = resembrar_demo(tenant.url, frozenset({"pack_agenda"}), _AHORA)
    assert conteos["citas"] > 0

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        n = _conteo(conn, "citas")
        # Fechas RELATIVAS: hay historial (antes de ahora) y agenda futura (después).
        rango = conn.execute("SELECT min(inicio) a, max(inicio) b FROM citas").fetchone()
        assert rango["a"] < _AHORA < rango["b"]
        # Estados creíbles: el pasado cierra (cumplida/no_show), el futuro está por venir.
        estados = {r["estado"] for r in conn.execute("SELECT DISTINCT estado FROM citas").fetchall()}
        assert {"cumplida"} <= estados
        assert estados <= {"cumplida", "no_show", "confirmada", "pendiente"}

    # Idempotente: re-correr con el MISMO `ahora` deja el mismo conteo (wipe + siembra determinista).
    assert resembrar_demo(tenant.url, frozenset({"pack_agenda"}), _AHORA)["citas"] == n
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        assert _conteo(conn, "citas") == n

    # Reset: con OTRO `ahora` (una semana después) se RESIEMBRA relativo al nuevo hoy, sin acumular.
    resembrar_demo(tenant.url, frozenset({"pack_agenda"}), _AHORA_SIG)
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        rango2 = conn.execute("SELECT min(inicio) a, max(inicio) b FROM citas").fetchone()
        # La ventana se MOVIÓ con `ahora` y TODO el dato cae dentro de ella (reset, no acumulación de
        # dos semanas): nada antes de ahora-8d ni después de ahora+6d del NUEVO hoy.
        assert _AHORA_SIG - timedelta(days=8) <= rango2["a"] < _AHORA_SIG < rango2["b"] <= _AHORA_SIG + timedelta(days=6)


async def test_reservas_prioridad_sobre_citas_y_multidia(tenant):
    # Un hotel tiene pack_agenda Y pack_reservas: deben sembrarse RESERVAS (noches), no citas por slot.
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_agenda(_agenda_hotel(), conn)
        conn.commit()

    conteos = resembrar_demo(tenant.url, frozenset({"pack_agenda", "pack_reservas"}), _AHORA)
    assert "reservas" in conteos and "citas" not in conteos
    assert conteos["reservas"] > 0

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        # Estadías multi-día: fin > inicio por al menos una noche; check-in 15:00, check-out 12:00.
        fila = conn.execute(
            "SELECT inicio, fin FROM citas ORDER BY inicio LIMIT 1"
        ).fetchone()
        assert fila["fin"] > fila["inicio"]
        assert fila["inicio"].astimezone(COLOMBIA_TZ).hour == 15
        assert fila["fin"].astimezone(COLOMBIA_TZ).hour == 12
        # Hay al menos una estadía EN CURSO hoy (cliente hospedado).
        en_curso = conn.execute(
            "SELECT count(*) n FROM citas WHERE inicio <= %s AND fin >= %s", (_AHORA, _AHORA)
        ).fetchone()["n"]
        assert en_curso >= 1


async def test_pedidos_kanban_hoy_con_items(tenant):
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_pos(
            PackPos(productos=[
                ProductoPos(nombre="Bandeja paisa", unidad_medida="plato", precio_venta=32000, iva=0),
                ProductoPos(nombre="Limonada", unidad_medida="vaso", precio_venta=7000, iva=0),
            ]),
            conn,
        )
        cargar_pedidos(
            PackPedidos(config=PedidoConfig(costo_domicilio_default=5000),
                        zonas=[ZonaDomicilio(nombre="Centro", tarifa=4000)]),
            conn,
        )
        conn.commit()

    conteos = resembrar_demo(tenant.url, frozenset({"pos", "pack_pedidos"}), _AHORA)
    assert conteos["pedidos"] > 0

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        # El kanban de HOY tiene los estados activos; el historial, entregados.
        estados = {r["estado"] for r in conn.execute("SELECT DISTINCT estado FROM pedidos").fetchall()}
        assert {"recibido", "en_camino", "entregado"} <= estados
        # Cada pedido tiene ítems con totales coherentes (subtotal de ítems + domicilio = total).
        assert _conteo(conn, "pedido_items") >= conteos["pedidos"]
        fila = conn.execute(
            "SELECT p.subtotal, p.total, p.costo_domicilio, "
            "(SELECT coalesce(sum(subtotal),0) FROM pedido_items WHERE pedido_id=p.id) suma_items "
            "FROM pedidos p ORDER BY p.id LIMIT 1"
        ).fetchone()
        assert fila["suma_items"] == fila["subtotal"]
        assert fila["total"] == fila["subtotal"] + fila["costo_domicilio"]

    # Idempotente.
    assert resembrar_demo(tenant.url, frozenset({"pos", "pack_pedidos"}), _AHORA)["pedidos"] == conteos["pedidos"]
