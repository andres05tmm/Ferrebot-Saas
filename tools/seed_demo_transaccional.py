"""Datos transaccionales VIVOS para tenants demo (plan §4-§5): citas/reservas/pedidos con fechas
RELATIVAS a hoy, para que la demo siempre amanezca llena y creíble.

Filosofía (plan §4 "las demos siempre amanecen impecables"): RESET a estado canónico. Cada seeder
**borra** primero su tabla transaccional y la vuelve a sembrar relativa a `ahora` (now_co). Eso lo
hace a la vez:
  - idempotente — re-correr con el mismo `ahora` da el mismo estado (wipe + siembra determinista);
  - la operación de resiembra nocturna — el cron lo llama con el `ahora` del día y la demo se renueva.

Nunca toca config ni catálogo (servicios, recursos, agenda_config, productos, pedido_config): eso lo
fija el manifiesto. Solo lo transaccional (citas, pedidos). Driver SYNC (psycopg, dict_row), como los
loaders de manifiesto; el commit lo hace `resembrar_demo`. Determinista vía `Random(ahora.toordinal())`
para que un test pueda afirmar conteos y la relatividad de las fechas.

SOLO para tenants demo: el llamador (provisionador / cron de resiembra) ya restringe a `demo_slugs`.
Borra datos: jamás apuntar esto a un tenant real.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.config.timezone import COLOMBIA_TZ, now_co
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger
from core.tenancy.catalogo import capacidades_completas
from tools.provision_tenant import _db_name, _features_efectivas

log = get_logger("seed_demo_transaccional")

# Nombres y teléfonos ficticios para clientes de la demo (estables; el RNG elige de aquí).
_CLIENTES = [
    ("Laura Gómez", "+57 300 1112233"), ("Andrés Ruiz", "+57 301 2223344"),
    ("Marcela Díaz", "+57 302 3334455"), ("Julián Torres", "+57 311 4445566"),
    ("Paola Mejía", "+57 312 5556677"), ("Camilo Rojas", "+57 313 6667788"),
    ("Daniela Castro", "+57 320 7778899"), ("Sebastián Páez", "+57 321 8889900"),
    ("Valentina Soto", "+57 350 9990011"), ("Felipe Moreno", "+57 351 0001122"),
]

_HORAS_DIA = [9, 10, 11, 12, 14, 15, 16, 17]   # horas pico de citas (sin overlap por hora distinta)


def _aware(ahora: datetime, dia_offset: int, hora: int, minuto: int = 0) -> datetime:
    """Datetime aware en hora Colombia, `dia_offset` días desde la fecha de `ahora`, a `hora:minuto`."""
    fecha = (ahora + timedelta(days=dia_offset)).date()
    return datetime(fecha.year, fecha.month, fecha.day, hora, minuto, tzinfo=COLOMBIA_TZ)


def _estado_cita(inicio: datetime, ahora: datetime, rng: random.Random) -> str:
    """Estado creíble según el momento: pasado → cumplida/no_show; hoy/futuro → confirmada/pendiente."""
    if inicio < ahora:
        return "no_show" if rng.random() < 0.18 else "cumplida"
    return "confirmada" if rng.random() < 0.6 else "pendiente"


def _recursos_con_servicios(conn, tipo: str) -> list[dict]:
    """Recursos de `tipo` con los servicios que prestan (id, duracion, precio). Vacío si no hay."""
    recursos = conn.execute(
        "SELECT id, nombre FROM recursos WHERE tipo = %s::recurso_tipo AND activo ORDER BY id",
        (tipo,),
    ).fetchall()
    salida: list[dict] = []
    for r in recursos:
        servicios = conn.execute(
            "SELECT s.id, s.duracion_min, s.precio FROM recurso_servicio rs "
            "JOIN servicios s ON s.id = rs.servicio_id WHERE rs.recurso_id = %s AND s.activo",
            (r["id"],),
        ).fetchall()
        if servicios:
            salida.append({"id": r["id"], "nombre": r["nombre"], "servicios": servicios})
    return salida


def _insertar_cita(conn, *, servicio_id, recurso_id, cliente, inicio, fin, estado, clave) -> None:
    """Inserta una cita demo. `idempotency_key` determinista (documenta intención; la tabla ya se vació)."""
    confirmacion = "reconfirmada" if estado == "confirmada" else "esperando"
    conn.execute(
        "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, inicio, fin, "
        "estado, origen, confirmacion, idempotency_key) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::cita_estado,'whatsapp',%s::cita_confirmacion,%s) "
        "ON CONFLICT (idempotency_key) DO NOTHING",
        (servicio_id, recurso_id, cliente[0], cliente[1], inicio, fin, estado, confirmacion, clave),
    )


def _sembrar_citas(conn, ahora: datetime) -> int:
    """Agenda por SLOTS (clínica/barbería): historial pasado + agenda de hoy + próximos días.

    Borra `citas` y siembra relativo a `ahora`: por recurso, 1-2 citas/día en [-7..-1] (historial),
    2-3 hoy y 1-2 en [+1..+5]. Cada cita en una hora distinta del día para no solaparse.
    """
    conn.execute("DELETE FROM citas")
    recursos = _recursos_con_servicios(conn, "profesional")
    if not recursos:
        return 0
    rng = random.Random(ahora.toordinal())
    n = 0
    for offset in range(-7, 6):
        if offset == 0:
            por_recurso = 3
        elif offset < 0:
            por_recurso = rng.randint(1, 2)
        else:
            por_recurso = rng.randint(1, 2)
        for r in recursos:
            horas = rng.sample(_HORAS_DIA, k=min(por_recurso, len(_HORAS_DIA)))
            for hora in horas:
                servicio = rng.choice(r["servicios"])
                inicio = _aware(ahora, offset, hora)
                fin = inicio + timedelta(minutes=int(servicio["duracion_min"]))
                estado = _estado_cita(inicio, ahora, rng)
                cliente = rng.choice(_CLIENTES)
                clave = f"demo:cita:{r['id']}:{inicio.isoformat()}"
                _insertar_cita(conn, servicio_id=servicio["id"], recurso_id=r["id"], cliente=cliente,
                               inicio=inicio, fin=fin, estado=estado, clave=clave)
                n += 1
    log.info("demo_citas_sembradas", n=n)
    return n


def _horas_checkin_checkout(conn) -> tuple[int, int]:
    """Horas de check-in/check-out de `agenda_config` (modo noches). Defaults 15:00 / 12:00."""
    fila = conn.execute("SELECT checkin_hora, checkout_hora FROM agenda_config WHERE id = 1").fetchone()
    if fila is None:
        return 15, 12
    return fila["checkin_hora"].hour, fila["checkout_hora"].hour


def _sembrar_reservas(conn, ahora: datetime) -> int:
    """Reservas por NOCHES (hotel): una reserva es una cita sobre un recurso tipo `habitacion`, con
    inicio=check-in y fin=check-out. Borra `citas` y siembra estadías: pasadas (cumplida), en curso
    (cliente hospedado hoy) y próximas (confirmada/pendiente), relativas a `ahora`."""
    conn.execute("DELETE FROM citas")
    habitaciones = _recursos_con_servicios(conn, "habitacion")
    if not habitaciones:
        return 0
    h_in, h_out = _horas_checkin_checkout(conn)
    rng = random.Random(ahora.toordinal())
    # (offset de check-in, noches): pasada, en curso (entró ayer, sale mañana), próxima cercana, futura.
    plantillas = [(-6, 2), (-1, 3), (2, 2), (5, 4)]
    n = 0
    for r in habitaciones:
        servicio = r["servicios"][0]                       # la habitación presta UN tipo (su precio/noche)
        # Cada habitación toma un subconjunto distinto de plantillas (ocupación dispar, creíble).
        elegidas = rng.sample(plantillas, k=rng.randint(2, len(plantillas)))
        for offset, noches in elegidas:
            inicio = _aware(ahora, offset, h_in)
            fin = _aware(ahora, offset + noches, h_out)
            estado = "cumplida" if fin < ahora else "confirmada" if inicio <= ahora else (
                "confirmada" if rng.random() < 0.6 else "pendiente"
            )
            cliente = rng.choice(_CLIENTES)
            clave = f"demo:reserva:{r['id']}:{inicio.isoformat()}"
            _insertar_cita(conn, servicio_id=servicio["id"], recurso_id=r["id"], cliente=cliente,
                           inicio=inicio, fin=fin, estado=estado, clave=clave)
            n += 1
    log.info("demo_reservas_sembradas", n=n)
    return n


# Pedidos de HOY por estado del kanban (los activos) + entregados de días pasados (historial).
_ESTADOS_HOY = ["recibido", "confirmado", "en_preparacion", "en_camino"]


def _sembrar_pedidos(conn, ahora: datetime) -> int:
    """Pedidos (restaurante): borra `pedidos`/`pedido_items` y siembra el kanban de HOY (un pedido por
    estado activo) + entregados de los últimos 3 días. Ítems desde el catálogo POS; totales coherentes."""
    conn.execute("DELETE FROM pedido_items")
    conn.execute("DELETE FROM pedidos")
    productos = conn.execute(
        "SELECT id, nombre, precio_venta FROM productos WHERE activo ORDER BY id"
    ).fetchall()
    if not productos:
        return 0
    cfg = conn.execute(
        "SELECT costo_domicilio_default FROM pedido_config ORDER BY id LIMIT 1"
    ).fetchone()
    costo_dom = Decimal(cfg["costo_domicilio_default"]) if cfg else Decimal(0)
    rng = random.Random(ahora.toordinal())

    # (offset, hora, estado): activos de hoy + entregados de los últimos 3 días.
    plan = [(0, ahora.hour if ahora.hour in range(11, 22) else 13, e) for e in _ESTADOS_HOY]
    for d in (1, 2, 3):
        for _ in range(rng.randint(1, 2)):
            plan.append((-d, rng.choice([12, 13, 19, 20]), "entregado"))

    n = 0
    for idx, (offset, hora, estado) in enumerate(plan):
        creado = _aware(ahora, offset, hora, rng.choice([5, 15, 25, 40]))
        _insertar_pedido(conn, productos, costo_dom, creado, estado, rng, idx)
        n += 1
    log.info("demo_pedidos_sembrados", n=n)
    return n


def _insertar_pedido(conn, productos, costo_dom, creado, estado, rng: random.Random, idx: int) -> None:
    """Un pedido con 1-3 ítems del catálogo (snapshot nombre/precio) y totales coherentes."""
    cliente = rng.choice(_CLIENTES)
    elegidos = rng.sample(productos, k=min(rng.randint(1, 3), len(productos)))
    subtotal = Decimal(0)
    lineas: list[tuple] = []
    for p in elegidos:
        cant = Decimal(rng.randint(1, 2))
        precio = Decimal(p["precio_venta"])
        linea = precio * cant
        subtotal += linea
        lineas.append((p["id"], p["nombre"], cant, precio, linea))
    total = subtotal + costo_dom
    # Sin ON CONFLICT: la idempotencia la da el wipe previo (la tabla quedó vacía). `idempotency_key`
    # determinista se conserva por trazabilidad/realismo (es el que pondría el agente al crear el pedido).
    pedido_id = conn.execute(
        "INSERT INTO pedidos (cliente_nombre, cliente_telefono, costo_domicilio, estado, subtotal, "
        "total, origen, idempotency_key, creado_en, actualizado_en) "
        "VALUES (%s,%s,%s,%s::pedido_estado,%s,%s,'whatsapp',%s,%s,%s) RETURNING id",
        (cliente[0], cliente[1], costo_dom, estado, subtotal, total,
         f"demo:pedido:{creado.isoformat()}:{idx}", creado, creado),
    ).fetchone()["id"]
    for producto_id, nombre, cant, precio, linea in lineas:
        conn.execute(
            "INSERT INTO pedido_items (pedido_id, producto_id, nombre, cantidad, precio_unitario, subtotal) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (pedido_id, producto_id, nombre, cant, precio, linea),
        )


def resembrar_demo(conn_url: str, capacidades: frozenset[str], ahora: datetime) -> dict[str, int]:
    """Resiembra los datos VIVOS de un tenant demo (reset a estado canónico). Devuelve conteos.

    Despacha por capacidad: reservas (noches) tiene prioridad sobre agenda (slots) — un hotel tiene
    ambas (pack_reservas depende de pack_agenda), pero sus datos son reservas, no citas por slot.
    Pedidos es independiente (restaurante). Un commit al final.
    """
    conteos: dict[str, int] = {}
    with psycopg.connect(to_libpq(conn_url), row_factory=dict_row) as conn:
        if "pack_reservas" in capacidades:
            conteos["reservas"] = _sembrar_reservas(conn, ahora)
        elif "pack_agenda" in capacidades:
            conteos["citas"] = _sembrar_citas(conn, ahora)
        if "pack_pedidos" in capacidades:
            conteos["pedidos"] = _sembrar_pedidos(conn, ahora)
        conn.commit()
    return conteos


def capacidades_efectivas_sync(slug: str) -> frozenset[str]:
    """Capacidades EFECTIVAS de un tenant leídas del control DB (driver SYNC, para el CLI/seed).

    Reúsa los helpers PUROS del catálogo (`_features_efectivas` + `capacidades_completas`) sobre el plan
    (`planes.limites.features`) y los overrides (`empresa_features`), igual que `cargar_plan_features`
    al escribir. El cron del worker usa la vía async (`ControlCapacidades.efectivas`) — mismo resultado.
    """
    with psycopg.connect(
        to_libpq(get_settings().control_database_url), row_factory=dict_row
    ) as conn:
        empresa = conn.execute(
            "SELECT e.id, p.limites FROM empresas e LEFT JOIN planes p ON p.id = e.plan_id "
            "WHERE e.slug = %s",
            (slug,),
        ).fetchone()
        if empresa is None:
            raise SystemExit(f"tenant no encontrado: {slug}")
        limites = empresa["limites"] or {}
        plan_features = list(limites.get("features", [])) if isinstance(limites, dict) else []
        overrides = {
            r["feature"]: r["habilitada"]
            for r in conn.execute(
                "SELECT feature, habilitada FROM empresa_features WHERE empresa_id = %s",
                (empresa["id"],),
            ).fetchall()
        }
    return capacidades_completas(_features_efectivas(plan_features, overrides))


def resembrar_slug(slug: str, ahora: datetime | None = None) -> dict[str, int]:
    """Resiembra un tenant demo por SLUG (resuelve caps + URL directa). Conveniencia para el CLI."""
    settings = get_settings()
    capacidades = capacidades_efectivas_sync(slug)
    conn_url = tenant_url(settings.tenants_direct_url_base, _db_name(slug))
    return resembrar_demo(conn_url, capacidades, ahora or now_co())


def main(argv: list[str] | None = None) -> int:
    """CLI: resiembra los datos vivos de uno o varios tenants demo (siembra inicial o reset manual).

    `--slug X` resiembra ese tenant; sin `--slug` resiembra TODOS los de `settings.demo_slugs`. El cron
    nocturno hace lo mismo automáticamente (apps.worker.main.resembrar_demos).
    """
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    parser = argparse.ArgumentParser(description="Resembrar datos vivos de tenants demo (ADR 0007 / plan §5).")
    parser.add_argument("--slug", help="Slug de UN tenant demo (omitir = todos los demo_slugs)")
    args = parser.parse_args(argv)

    slugs = [args.slug] if args.slug else list(get_settings().demo_slugs)
    ahora = now_co()
    for slug in slugs:
        conteos = resembrar_slug(slug, ahora)
        print(f"resembrar_demo: {slug} OK -> {conteos}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
