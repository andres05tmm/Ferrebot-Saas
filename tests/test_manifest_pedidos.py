"""Loader del pack Pedidos (ADR 0016) + checkin/checkout de agenda: integración contra BD efímera.

`cargar_pedidos` siembra `pedido_config` (una fila) + `zonas_domicilio`; re-correr no duplica ni
cambia conteos (idempotencia, requisito DURO). Además: el loader de agenda persiste
`checkin_hora`/`checkout_hora` (modo reservas/noches, migración tenant 0022) leídos del manifiesto.

Driver SYNC (psycopg con dict_row), igual que el provisionador; `tenant` da una app DB migrada.
"""
from __future__ import annotations

from datetime import time

import psycopg
from psycopg.rows import dict_row

from core.db.urls import to_libpq
from tools.manifest.packs.agenda import cargar_agenda
from tools.manifest.packs.pedidos import cargar_pedidos
from tools.manifest.schema import (
    AgendaConfig,
    PackAgenda,
    PackPedidos,
    PedidoConfig,
    ZonaDomicilio,
)


def _conteo(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


def _pack_pedidos() -> PackPedidos:
    return PackPedidos(
        config=PedidoConfig(
            hora_apertura="11:00", hora_cierre="22:00", minimo_pedido=20000,
            tiempo_estimado_min=35, costo_domicilio_default=4000,
        ),
        zonas=[
            ZonaDomicilio(nombre="Centro", tarifa=3000),
            ZonaDomicilio(nombre="Norte", tarifa=5000),
        ],
    )


async def test_cargar_pedidos_siembra_y_es_idempotente(tenant):
    pedidos = _pack_pedidos()
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_pedidos(pedidos, conn)
        conn.commit()

        assert _conteo(conn, "pedido_config") == 1
        assert _conteo(conn, "zonas_domicilio") == 2

        cfg = conn.execute("SELECT * FROM pedido_config ORDER BY id LIMIT 1").fetchone()
        assert cfg["hora_apertura"] == time(11, 0)
        assert cfg["hora_cierre"] == time(22, 0)
        assert int(cfg["minimo_pedido"]) == 20000               # MONEY → Decimal
        assert cfg["tiempo_estimado_min"] == 35
        assert int(cfg["costo_domicilio_default"]) == 4000

        norte = conn.execute(
            "SELECT tarifa, activo FROM zonas_domicilio WHERE nombre = %s", ("Norte",)
        ).fetchone()
        assert int(norte["tarifa"]) == 5000 and norte["activo"] is True

        # Idempotencia: re-correr no duplica la fila de config ni las zonas, y propaga un cambio.
        pedidos.config.minimo_pedido = 25000
        pedidos.zonas[0].tarifa = 3500
        cargar_pedidos(pedidos, conn)
        conn.commit()
        assert _conteo(conn, "pedido_config") == 1
        assert _conteo(conn, "zonas_domicilio") == 2
        cfg2 = conn.execute("SELECT minimo_pedido FROM pedido_config ORDER BY id LIMIT 1").fetchone()
        assert int(cfg2["minimo_pedido"]) == 25000
        centro = conn.execute(
            "SELECT tarifa FROM zonas_domicilio WHERE nombre = %s", ("Centro",)
        ).fetchone()
        assert int(centro["tarifa"]) == 3500


async def test_cargar_agenda_persiste_checkin_checkout(tenant):
    # Modo reservas/noches: el manifiesto fija las horas que convierten "N noches" en [checkin, checkout).
    agenda = PackAgenda(
        config=AgendaConfig(checkin_hora="14:00", checkout_hora="11:00"),
        servicios=[],
        recursos=[],
    )
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_agenda(agenda, conn)
        conn.commit()
        cfg = conn.execute("SELECT checkin_hora, checkout_hora FROM agenda_config WHERE id = 1").fetchone()
        assert cfg["checkin_hora"] == time(14, 0)
        assert cfg["checkout_hora"] == time(11, 0)
