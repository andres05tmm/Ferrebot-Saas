"""Los manifiestos DEMO versionados parsean, validan y tienen la forma esperada por vertical. PURO.

Cada demo es un tenant real provisionado por manifiesto (plan §5): este test es la red que evita que un
manifiesto demo se rompa silenciosamente (lo que volvería la demo invendible). No toca la BD: solo
parseo + validación + forma, igual que `test_manifest`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.tenancy.catalogo import capacidades_completas
from tools.manifest import Manifiesto, cargar_manifiesto, validar
from tools.manifest.packs.registry import packs_activos
from tools.provision_tenant import _features_efectivas

_ONBOARDING = Path(__file__).parents[1] / "tools" / "onboarding"


def _ruta(slug: str) -> Path:
    return _ONBOARDING / f"{slug}.manifest.example.yaml"


def _efectivas(m: Manifiesto) -> frozenset[str]:
    # Espeja el camino real del provisionador: NUCLEO ∪ (plan ± overrides) con meta-packs expandidos.
    plan_features = list(m.plan.features) if m.plan else []
    return capacidades_completas(_features_efectivas(plan_features, m.features_override))


DEMOS = ["barberia-demo", "restaurante-demo", "hotel-demo", "peluqueria-demo"]


@pytest.mark.parametrize("slug", DEMOS)
def test_demo_parsea_y_valida(slug: str):
    m = cargar_manifiesto(_ruta(slug))
    validar(m)  # no lanza
    assert m.identidad.slug == slug
    # Toda plantilla declara su rubro (persona del bot; sin él caería al prompt ferretero).
    assert m.identidad.rubro
    # Cada demo trae su identidad demo no-admin (rol vendedor) con el email demo+<slug>@melquiadez.com.
    demo = [i for i in m.identidades if i.email == f"demo+{slug}@melquiadez.com"]
    assert len(demo) == 1 and demo[0].rol == "vendedor"
    # Y un admin con email (login real).
    assert m.admin.email and m.admin.email.endswith(f"@{slug}.melquiadez.com")
    # Canal WhatsApp mapeado (lo re-apunta switch_demo al mostrar).
    assert m.canal.whatsapp is not None


def test_barberia_es_agenda_pura():
    m = cargar_manifiesto(_ruta("barberia-demo"))
    efectivas = _efectivas(m)
    # Sin el meta-pack `pos`; su contable de servicios son las finas caja+ventas (ADR 0021).
    assert "pack_agenda" in efectivas and "pos" not in efectivas
    assert {"caja", "ventas"} <= efectivas and "inventario" not in efectivas
    assert m.packs.agenda is not None
    assert len(m.packs.agenda.recursos) == 3                     # 3 barberos
    assert len(m.packs.agenda.servicios) >= 5
    # Todos los recursos son barberos (profesional) con disponibilidad declarada (L–S).
    assert all(r.tipo == "profesional" and r.disponibilidad for r in m.packs.agenda.recursos)


def test_restaurante_tiene_menu_pos_y_pedidos():
    m = cargar_manifiesto(_ruta("restaurante-demo"))
    efectivas = _efectivas(m)
    assert {"pos", "pack_pedidos"} <= efectivas
    assert m.packs.pos is not None and len(m.packs.pos.productos) >= 25   # menú ~25 ítems
    assert m.packs.pedidos is not None and len(m.packs.pedidos.zonas) >= 1
    # Impuestos (ADR 0032 D2): tipo 'iva' admite 0/5/19; el impoconsumo va como tipo 'inc' tarifa 8.
    assert all(
        (p.tipo_impuesto == "iva" and p.iva in {0, 5, 19})
        or (p.tipo_impuesto == "inc" and p.iva == 8)
        for p in m.packs.pos.productos
    )
    # Pack Restaurante (ADR 0032): la demo trae modificadores, receta, KDS y mesas.
    con_mods = [p for p in m.packs.pos.productos if p.modificadores]
    assert con_mods, "la demo debe traer platos con modificadores"
    assert any(p.receta for p in m.packs.pos.productos), "la demo debe traer un plato con receta"
    assert any(p.zona_comanda for p in m.packs.pos.productos)
    assert len(m.packs.pedidos.mesas) >= 3
    assert {"pack_mesas", "kds", "menu_qr", "recetas"} <= _efectivas(m)
    # pack_pedidos corre como pack con loader (config + zonas); el menú lo siembra el pack `ventas`
    # (ADR 0021: hereda el loader del catálogo; `pos` expande a la fina en el set efectivo).
    flags = {p.flag for p in packs_activos(efectivas | {"clientes", "reportes"})}
    assert {"ventas", "pack_pedidos"} <= flags


def test_peluqueria_es_el_carril_contable_de_servicios():
    # ADR 0021/0022: agenda + finas caja/ventas SIN pos ni inventario — el "contable configurable".
    m = cargar_manifiesto(_ruta("peluqueria-demo"))
    features = set(m.plan.features)
    assert {"pack_agenda", "caja", "ventas"} <= features
    assert "pos" not in features and "inventario" not in features
    # Servicios con precio (cobrables con un clic, ADR 0022) y mostrador sin stock_inicial.
    assert m.packs.agenda is not None
    assert all(s.precio and s.precio > 0 for s in m.packs.agenda.servicios)
    assert m.packs.pos is not None and len(m.packs.pos.productos) >= 3
    efectivas = _efectivas(m)
    assert "ventas" in efectivas          # habilita la sección packs.pos sin el meta-pack
    assert m.identidad.rubro == "peluquería"


def test_hotel_es_reservas_sobre_agenda():
    m = cargar_manifiesto(_ruta("hotel-demo"))
    efectivas = _efectivas(m)
    assert {"pack_agenda", "pack_reservas"} <= efectivas
    assert m.packs.agenda is not None
    # Habitaciones = recursos tipo `habitacion`; cada una presta un tipo (su precio/noche).
    habitaciones = [r for r in m.packs.agenda.recursos if r.tipo == "habitacion"]
    assert len(habitaciones) >= 4 and len(habitaciones) == len(m.packs.agenda.recursos)
    assert all(len(r.presta) == 1 for r in habitaciones)
    # check-in/check-out fijados para el modo noches.
    assert m.packs.agenda.config.checkin_hora == "15:00"
    assert m.packs.agenda.config.checkout_hora == "12:00"
    # Todos los tipos de habitación tienen precio (= precio por noche).
    assert all(s.precio and s.precio > 0 for s in m.packs.agenda.servicios)
