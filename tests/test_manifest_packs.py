"""Loaders de pack (ADR 0007 fase 2): integración contra una BD efímera (Postgres real).

Siembra desde `packs.agenda` / `packs.faq` del manifiesto de EJEMPLO, verifica conteos y relaciones,
y RE-CORRE cada loader comprobando que los conteos NO cambian (idempotencia, requisito DURO). Driver
SYNC (psycopg con dict_row), igual que el provisionador; el `tenant` fixture da una app DB migrada.
"""
from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from core.db.urls import to_libpq
from tools.manifest import cargar_manifiesto
from tools.manifest.packs.agenda import cargar_agenda
from tools.manifest.packs.faq import cargar_faq
from tools.manifest.packs.registry import PACKS, packs_activos

_EJEMPLO = Path(__file__).parents[1] / "tools" / "onboarding" / "clinica-demo.manifest.example.yaml"


def _conteo(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


async def test_cargar_agenda_siembra_y_es_idempotente(tenant):
    manifiesto = cargar_manifiesto(_EJEMPLO)
    agenda = manifiesto.packs.agenda
    assert agenda is not None

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_agenda(agenda, conn)
        conn.commit()

        # Conteos base.
        assert _conteo(conn, "servicios") == 3
        assert _conteo(conn, "recursos") == 2
        assert _conteo(conn, "recurso_servicio") == 3      # 2 (Dra. García) + 1 (Lic. Martínez)
        assert _conteo(conn, "disponibilidad") == 20       # 2 recursos × 5 días × 2 franjas
        assert _conteo(conn, "agenda_config") == 1

        # Relación recurso → servicios vía recurso_servicio (resolución nombre→id).
        servicios_garcia = {
            r["nombre"] for r in conn.execute(
                "SELECT s.nombre FROM recurso_servicio rs "
                "JOIN recursos rc ON rc.id = rs.recurso_id "
                "JOIN servicios s ON s.id = rs.servicio_id "
                "WHERE rc.nombre = %s",
                ("Dra. García",),
            ).fetchall()
        }
        assert servicios_garcia == {"Limpieza dental", "Blanqueamiento"}

        # Filas de disponibilidad por recurso: 10 cada uno (5 días × 2 franjas).
        por_recurso = conn.execute(
            "SELECT rc.nombre, count(*) AS n FROM disponibilidad d "
            "JOIN recursos rc ON rc.id = d.recurso_id GROUP BY rc.nombre"
        ).fetchall()
        assert {r["nombre"]: r["n"] for r in por_recurso} == {"Dra. García": 10, "Lic. Martínez": 10}

        # agenda_config: fila única id=1 con los valores del manifiesto.
        cfg = conn.execute("SELECT * FROM agenda_config WHERE id = 1").fetchone()
        assert cfg["modo_confirmacion"] == "manual"
        assert cfg["persona"] and "Clínica Demo" in cfg["persona"]
        assert cfg["recordatorios_horas"] == [24, 2]
        # precio MONEY → Decimal (no int).
        precio = conn.execute(
            "SELECT precio FROM servicios WHERE nombre = %s", ("Limpieza dental",)
        ).fetchone()["precio"]
        assert int(precio) == 80000

        # Idempotencia: re-correr no cambia ningún conteo.
        antes = {t: _conteo(conn, t) for t in PACKS["pack_agenda"].tablas}
        cargar_agenda(agenda, conn)
        conn.commit()
        despues = {t: _conteo(conn, t) for t in PACKS["pack_agenda"].tablas}
        assert antes == despues


async def test_cargar_faq_siembra_y_es_idempotente(tenant):
    manifiesto = cargar_manifiesto(_EJEMPLO)
    faq = manifiesto.packs.faq
    assert faq is not None

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_faq(faq, conn)
        conn.commit()

        assert _conteo(conn, "conocimiento") == 4
        fila = conn.execute(
            "SELECT contenido, orden, activo FROM conocimiento WHERE titulo = %s",
            ("Formas de pago",),
        ).fetchone()
        assert fila["activo"] is True and fila["orden"] == 2
        assert "transferencia" in fila["contenido"]

        # Idempotencia: re-correr no duplica.
        cargar_faq(faq, conn)
        conn.commit()
        assert _conteo(conn, "conocimiento") == 4


def test_packs_activos_filtra_por_set_efectivo():
    # PURO (sin BD): el registro expone solo los packs cuyo flag está activo. Desde ADR 0021
    # `ventas` ES un pack (hereda el loader del catálogo POS); `canal_whatsapp` no lo es.
    activos = packs_activos(frozenset({"pack_agenda", "ventas", "canal_whatsapp"}))
    assert {p.flag for p in activos} == {"ventas", "pack_agenda"}

    ambos = packs_activos(frozenset({"pack_agenda", "pack_faq"}))
    assert {p.flag for p in ambos} == {"pack_agenda", "pack_faq"}

    assert [p.flag for p in packs_activos(frozenset({"ventas"}))] == ["ventas"]
    assert packs_activos(frozenset({"canal_whatsapp"})) == []
