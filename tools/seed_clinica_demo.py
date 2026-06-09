"""DEPRECADO (ADR 0007 §D6). Usa el manifiesto declarativo + el provisionador de un paso:

    python -m tools.provision_from_manifest --from tools/onboarding/clinica-demo.manifest.example.yaml

`clinica-demo.manifest.example.yaml` es ahora el manifiesto canónico de clinica-demo (mismos valores
de agenda que este seed: la prueba `tests/test_manifest_aceptacion.py` afirma filas idénticas). Este
módulo queda SOLO porque la prueba de aceptación reusa `seed_agenda` como referencia bespoke; no lo
uses para dar de alta tenants nuevos.

----------------------------------------------------------------------------
Siembra una CLÍNICA DEMO completa en su propio tenant de prueba (NO Punto Rojo). Idempotente.

Materializa el ejemplo de `docs/pack-agenda-citas.md`: 2 profesionales, 3 servicios con precio/buffers,
sus asignaciones, disponibilidad L–V (mañana y tarde) y las reglas de la agenda (modo manual). Además
enciende los flags `pack_agenda` + `canal_whatsapp` para la empresa en el control DB.

Aprovisiona el tenant si no existe (DB propia `ferrebot_clinica-demo`, migrada). Re-ejecutar no duplica.

NUNCA toca Punto Rojo: trabaja exclusivamente sobre el slug `clinica-demo`.
"""
import sys

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger
from tools.provision_tenant import cargar_plan_features, provision_tenant

log = get_logger("seed_clinica_demo")

SLUG = "clinica-demo"
NOMBRE = "Clínica Demo"
NIT = "900900900"

# (nombre, tipo) — la especialidad (Odontología/Consulta) no se modela; va implícita en el servicio.
RECURSOS = [
    ("Dra. García", "profesional"),
    ("Lic. Martínez", "profesional"),
]

# nombre → (duracion_min, precio, buffer_antes, buffer_despues, categoria)
SERVICIOS = {
    "Limpieza dental": (40, 80000, 0, 10, "Odontología"),
    "Blanqueamiento": (60, 200000, 0, 10, "Odontología"),
    "Consulta": (30, 50000, 0, 0, "General"),
}

# recurso → servicios que presta (la odontóloga hace los de odontología; el de consulta, la consulta).
ASIGNACIONES = {
    "Dra. García": ["Limpieza dental", "Blanqueamiento"],
    "Lic. Martínez": ["Consulta"],
}

# Disponibilidad L–V (dia_semana 0=lunes … 4=viernes), mañana y tarde, para AMBOS recursos.
DIAS_LV = [0, 1, 2, 3, 4]
FRANJAS = [("08:00", "12:00"), ("14:00", "18:00")]

PERSONA = (
    "Eres el asistente de citas de la Clínica Demo. Hablas cordial, claro y profesional; tuteas con "
    "respeto y confirmas siempre el servicio, la fecha y el nombre antes de agendar."
)

AGENDA_CONFIG = {
    "zona_horaria": "America/Bogota",
    "intervalo_slots_min": 15,
    "anticipacion_minima_min": 120,
    "ventana_maxima_dias": 30,
    "politica_cancelacion_horas": 24,
    "permite_reagendar": True,
    "modo_confirmacion": "manual",
    "requiere_anticipo": False,
    "capacidad_por_slot": 1,
    "recordatorios_horas": [24, 2],
    "persona": PERSONA,
}


def _id_por_nombre(conn, tabla: str, nombre: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {tabla} WHERE nombre = %s", (nombre,)).fetchone()
    return row["id"] if row else None


def _seed_servicios(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    for nombre, (dur, precio, ba, bd, cat) in SERVICIOS.items():
        existente = _id_por_nombre(conn, "servicios", nombre)
        if existente is not None:
            ids[nombre] = existente
            continue
        ids[nombre] = conn.execute(
            "INSERT INTO servicios (nombre, duracion_min, precio, buffer_antes_min, "
            "buffer_despues_min, categoria, activo) VALUES (%s,%s,%s,%s,%s,%s,true) RETURNING id",
            (nombre, dur, precio, ba, bd, cat),
        ).fetchone()["id"]
    return ids


def _seed_recursos(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    for nombre, tipo in RECURSOS:
        existente = _id_por_nombre(conn, "recursos", nombre)
        if existente is not None:
            ids[nombre] = existente
            continue
        ids[nombre] = conn.execute(
            "INSERT INTO recursos (nombre, tipo, activo) VALUES (%s, %s::recurso_tipo, true) RETURNING id",
            (nombre, tipo),
        ).fetchone()["id"]
    return ids


def _seed_asignaciones(conn, recursos: dict[str, int], servicios: dict[str, int]) -> None:
    for recurso_nombre, lista in ASIGNACIONES.items():
        for servicio_nombre in lista:
            conn.execute(
                "INSERT INTO recurso_servicio (recurso_id, servicio_id) VALUES (%s,%s) "
                "ON CONFLICT DO NOTHING",
                (recursos[recurso_nombre], servicios[servicio_nombre]),
            )


def _seed_disponibilidad(conn, recursos: dict[str, int]) -> None:
    for recurso_id in recursos.values():
        for dia in DIAS_LV:
            for hi, hf in FRANJAS:
                ya = conn.execute(
                    "SELECT 1 FROM disponibilidad WHERE recurso_id=%s AND dia_semana=%s "
                    "AND hora_inicio=%s AND hora_fin=%s",
                    (recurso_id, dia, hi, hf),
                ).fetchone()
                if ya is None:
                    conn.execute(
                        "INSERT INTO disponibilidad (recurso_id, dia_semana, hora_inicio, hora_fin) "
                        "VALUES (%s,%s,%s,%s)",
                        (recurso_id, dia, hi, hf),
                    )


def _seed_agenda_config(conn) -> None:
    c = AGENDA_CONFIG
    conn.execute(
        "INSERT INTO agenda_config (id, zona_horaria, intervalo_slots_min, anticipacion_minima_min, "
        "ventana_maxima_dias, politica_cancelacion_horas, permite_reagendar, modo_confirmacion, "
        "requiere_anticipo, capacidad_por_slot, recordatorios_horas, persona) "
        "VALUES (1, %(zona_horaria)s, %(intervalo_slots_min)s, %(anticipacion_minima_min)s, "
        "%(ventana_maxima_dias)s, %(politica_cancelacion_horas)s, %(permite_reagendar)s, "
        "%(modo_confirmacion)s::modo_confirmacion, %(requiere_anticipo)s, %(capacidad_por_slot)s, "
        "%(recordatorios_horas)s, %(persona)s) "
        "ON CONFLICT (id) DO UPDATE SET "
        "zona_horaria=EXCLUDED.zona_horaria, intervalo_slots_min=EXCLUDED.intervalo_slots_min, "
        "anticipacion_minima_min=EXCLUDED.anticipacion_minima_min, "
        "ventana_maxima_dias=EXCLUDED.ventana_maxima_dias, "
        "politica_cancelacion_horas=EXCLUDED.politica_cancelacion_horas, "
        "permite_reagendar=EXCLUDED.permite_reagendar, modo_confirmacion=EXCLUDED.modo_confirmacion, "
        "requiere_anticipo=EXCLUDED.requiere_anticipo, capacidad_por_slot=EXCLUDED.capacidad_por_slot, "
        "recordatorios_horas=EXCLUDED.recordatorios_horas, persona=EXCLUDED.persona, "
        "actualizado_en=now()",
        c,
    )


def seed_agenda(tenant_conn_url: str) -> None:
    """Siembra catálogo + disponibilidad + reglas en la BD del tenant. Idempotente."""
    with psycopg.connect(to_libpq(tenant_conn_url), row_factory=dict_row) as conn:
        servicios = _seed_servicios(conn)
        recursos = _seed_recursos(conn)
        _seed_asignaciones(conn, recursos, servicios)
        _seed_disponibilidad(conn, recursos)
        _seed_agenda_config(conn)
        conn.commit()
    log.info("clinica_demo_sembrada", servicios=len(servicios), recursos=len(recursos))


def main() -> int:
    # La consola de Windows usa cp1252: los símbolos (✅) revientan con UnicodeEncodeError. Forzar UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    settings = get_settings()

    # 1) Tenant de prueba (idempotente): crea/asegura la empresa y su BD propia, migrada.
    empresa_id = provision_tenant(SLUG, NOMBRE, NIT, admin_nombre="Admin Demo")

    # 2) Flags del pack y del canal para esta empresa (override en empresa_features, validado).
    cargar_plan_features(empresa_id, {"features_override": {"pack_agenda": True, "canal_whatsapp": True}})

    # 3) Datos de la clínica en su BD.
    conn_url = tenant_url(settings.tenants_direct_url_base, f"ferrebot_{SLUG}")
    seed_agenda(conn_url)

    print(f"\n✅ Clínica demo lista — slug del tenant: {SLUG}  (empresa_id={empresa_id})")
    print(f"   Flags encendidos: pack_agenda, canal_whatsapp")
    print("\n   Para mapear el número de WhatsApp de Kapso a este tenant:")
    print(f"   python -m tools.seed_wa_numero <phone_number_id> {SLUG}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
