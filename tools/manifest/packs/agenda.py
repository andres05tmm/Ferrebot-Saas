"""Loader idempotente del pack Agenda (ADR 0007 fase 2).

Reproduce el patrón de inserts de `tools/seed_clinica_demo` (orden, casts `::recurso_tipo` /
`::modo_confirmacion`, `agenda_config` de una fila id=1) pero leyendo del manifiesto declarativo en
vez de constantes hardcodeadas. Driver SYNC; la `conn` debe traer `row_factory=dict_row` (como
abre `provision_tenant` / `seed_clinica_demo`).

Orden de escritura (respeta las FKs): servicios → recursos → recurso_servicio → disponibilidad →
agenda_config. Idempotencia por clave natural:
- servicios/recursos: por `nombre` (insert-si-ausente, paridad con el seed bespoke — ADR §D6).
- recurso_servicio: `ON CONFLICT DO NOTHING` (PK compuesta).
- disponibilidad: dedup por (recurso_id, dia_semana, hora_inicio, hora_fin).
- agenda_config: UPSERT de la única fila id=1.

Dinero (precio, anticipo_valor) va a columnas MONEY → se castea a Decimal al escribir, no int.
"""
from __future__ import annotations

from decimal import Decimal

from core.logging import get_logger
from tools.manifest.schema import AgendaConfig, PackAgenda

log = get_logger("manifest.packs.agenda")


def _id_por_nombre(conn, tabla: str, nombre: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {tabla} WHERE nombre = %s", (nombre,)).fetchone()
    return row["id"] if row else None


def _cargar_servicios(conn, agenda: PackAgenda) -> dict[str, int]:
    ids: dict[str, int] = {}
    for s in agenda.servicios:
        existente = _id_por_nombre(conn, "servicios", s.nombre)
        if existente is not None:
            ids[s.nombre] = existente
            continue
        precio = Decimal(s.precio) if s.precio is not None else None  # MONEY → Decimal
        ids[s.nombre] = conn.execute(
            "INSERT INTO servicios (nombre, duracion_min, precio, buffer_antes_min, "
            "buffer_despues_min, categoria, descripcion, activo) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,true) RETURNING id",
            (s.nombre, s.duracion_min, precio, s.buffer_antes_min, s.buffer_despues_min,
             s.categoria, s.descripcion),
        ).fetchone()["id"]
    return ids


def _cargar_recursos(conn, agenda: PackAgenda) -> dict[str, int]:
    ids: dict[str, int] = {}
    for r in agenda.recursos:
        existente = _id_por_nombre(conn, "recursos", r.nombre)
        if existente is not None:
            ids[r.nombre] = existente
            continue
        ids[r.nombre] = conn.execute(
            "INSERT INTO recursos (nombre, tipo, activo) VALUES (%s, %s::recurso_tipo, true) RETURNING id",
            (r.nombre, r.tipo),
        ).fetchone()["id"]
    return ids


def _cargar_asignaciones(conn, agenda: PackAgenda, recursos: dict[str, int],
                         servicios: dict[str, int]) -> None:
    """recurso.presta (nombres) → recurso_servicio (ids). ON CONFLICT DO NOTHING (idempotente)."""
    for r in agenda.recursos:
        for servicio_nombre in r.presta:
            conn.execute(
                "INSERT INTO recurso_servicio (recurso_id, servicio_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                (recursos[r.nombre], servicios[servicio_nombre]),
            )


def _cargar_disponibilidad(conn, agenda: PackAgenda, recursos: dict[str, int]) -> int:
    """Expande cada franja "HH:MM-HH:MM" × cada día de `dias[]` a una fila. Dedup por clave natural."""
    filas = 0
    for r in agenda.recursos:
        recurso_id = recursos[r.nombre]
        for disp in r.disponibilidad:
            for dia in disp.dias:
                for franja in disp.franjas:
                    hora_inicio, hora_fin = franja.split("-")
                    ya = conn.execute(
                        "SELECT 1 FROM disponibilidad WHERE recurso_id=%s AND dia_semana=%s "
                        "AND hora_inicio=%s AND hora_fin=%s",
                        (recurso_id, dia, hora_inicio, hora_fin),
                    ).fetchone()
                    if ya is None:
                        conn.execute(
                            "INSERT INTO disponibilidad (recurso_id, dia_semana, hora_inicio, hora_fin) "
                            "VALUES (%s,%s,%s,%s)",
                            (recurso_id, dia, hora_inicio, hora_fin),
                        )
                        filas += 1
    return filas


def _upsert_agenda_config(conn, cfg: AgendaConfig) -> None:
    """UPSERT de la única fila id=1 (CHECK en la migración). recordatorios_horas → array int."""
    params = {
        "zona_horaria": cfg.zona_horaria,
        "intervalo_slots_min": cfg.intervalo_slots_min,
        "anticipacion_minima_min": cfg.anticipacion_minima_min,
        "ventana_maxima_dias": cfg.ventana_maxima_dias,
        "politica_cancelacion_horas": cfg.politica_cancelacion_horas,
        "corte_riesgo_horas": cfg.corte_riesgo_horas,
        "permite_reagendar": cfg.permite_reagendar,
        "modo_confirmacion": cfg.modo_confirmacion,
        "requiere_anticipo": cfg.requiere_anticipo,
        "anticipo_tipo": cfg.anticipo_tipo,
        # MONEY → Decimal (no int); opcional (cobro futuro).
        "anticipo_valor": Decimal(cfg.anticipo_valor) if cfg.anticipo_valor is not None else None,
        "capacidad_por_slot": cfg.capacidad_por_slot,
        "recordatorios_horas": cfg.recordatorios_horas,  # psycopg adapta list[int] → array
        "persona": cfg.persona,
        "google_calendar_id": cfg.google_calendar_id,
        # Modo reservas/noches (0022): "HH:MM" → columna Time (PG castea el texto, igual que las franjas).
        "checkin_hora": cfg.checkin_hora,
        "checkout_hora": cfg.checkout_hora,
    }
    conn.execute(
        "INSERT INTO agenda_config (id, zona_horaria, intervalo_slots_min, anticipacion_minima_min, "
        "ventana_maxima_dias, politica_cancelacion_horas, corte_riesgo_horas, permite_reagendar, "
        "modo_confirmacion, requiere_anticipo, anticipo_tipo, anticipo_valor, capacidad_por_slot, "
        "recordatorios_horas, persona, google_calendar_id, checkin_hora, checkout_hora) "
        "VALUES (1, %(zona_horaria)s, %(intervalo_slots_min)s, %(anticipacion_minima_min)s, "
        "%(ventana_maxima_dias)s, %(politica_cancelacion_horas)s, %(corte_riesgo_horas)s, "
        "%(permite_reagendar)s, %(modo_confirmacion)s::modo_confirmacion, %(requiere_anticipo)s, "
        "%(anticipo_tipo)s::anticipo_tipo, %(anticipo_valor)s, %(capacidad_por_slot)s, "
        "%(recordatorios_horas)s, %(persona)s, %(google_calendar_id)s, "
        "%(checkin_hora)s, %(checkout_hora)s) "
        "ON CONFLICT (id) DO UPDATE SET "
        "zona_horaria=EXCLUDED.zona_horaria, intervalo_slots_min=EXCLUDED.intervalo_slots_min, "
        "anticipacion_minima_min=EXCLUDED.anticipacion_minima_min, "
        "ventana_maxima_dias=EXCLUDED.ventana_maxima_dias, "
        "politica_cancelacion_horas=EXCLUDED.politica_cancelacion_horas, "
        "corte_riesgo_horas=EXCLUDED.corte_riesgo_horas, permite_reagendar=EXCLUDED.permite_reagendar, "
        "modo_confirmacion=EXCLUDED.modo_confirmacion, requiere_anticipo=EXCLUDED.requiere_anticipo, "
        "anticipo_tipo=EXCLUDED.anticipo_tipo, anticipo_valor=EXCLUDED.anticipo_valor, "
        "capacidad_por_slot=EXCLUDED.capacidad_por_slot, recordatorios_horas=EXCLUDED.recordatorios_horas, "
        "persona=EXCLUDED.persona, google_calendar_id=EXCLUDED.google_calendar_id, "
        "checkin_hora=EXCLUDED.checkin_hora, checkout_hora=EXCLUDED.checkout_hora, "
        "actualizado_en=now()",
        params,
    )


def cargar_agenda(agenda: PackAgenda, conn) -> dict[str, int]:
    """Upserta el pack Agenda sobre la BD del tenant (idempotente). Devuelve conteos para el resumen.

    `conn` es una conexión psycopg SYNC con `row_factory=dict_row`; el commit lo hace el llamador
    (el provisionador envuelve toda la coreografía en una sola transacción por tenant).
    """
    servicios = _cargar_servicios(conn, agenda)
    recursos = _cargar_recursos(conn, agenda)
    _cargar_asignaciones(conn, agenda, recursos, servicios)
    _cargar_disponibilidad(conn, agenda, recursos)
    _upsert_agenda_config(conn, agenda.config)
    conteos = {"servicios": len(servicios), "recursos": len(recursos)}
    log.info("pack_agenda_cargado", **conteos)
    return conteos
