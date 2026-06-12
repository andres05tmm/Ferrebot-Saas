"""Modo demo (Fase 3c): siembra un tenant de showcase POR VERTICAL para demostrar sin cliente real.

Reusa el provisionador (`provision_tenant` + `cargar_plan_features` + `cargar_secretos_empresa`) y
encima siembra datos que dejan el dashboard del agente "vivo": catálogo de agenda, citas de hoy,
conversaciones con hilo (en `conversacion_mensajes`) para el inbox, y encuestas para el KPI de
satisfacción. Cada vertical trae su tema (`branding.tema`) → el mismo producto con otra cara.

Verticales (slug → tema): clinica-demo→aurora · restaurante-demo→brasa · hotel-demo→brisa ·
generico-demo→lienzo · barberia-demo→navaja.

Idempotente: el catálogo es get-or-create; los datos de showcase (citas, conversaciones, mensajes,
encuestas) se BORRAN y re-siembran (así las horas quedan "de hoy" al re-ejecutar). Acotado SIEMPRE a la
base del tenant demo — NUNCA toca Punto Rojo (guarda explícita por slug).

Uso:
    python -m tools.seed_demo                 # los 5 verticales
    python -m tools.seed_demo --only barberia-demo
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import psycopg
from psycopg.rows import dict_row

from core.config import get_settings
from core.config.timezone import COLOMBIA_TZ, now_co, today_co
from core.db.urls import tenant_url, to_libpq
from core.logging import configure_logging, get_logger
from tools.provision_tenant import cargar_plan_features, cargar_secretos_empresa, provision_tenant

log = get_logger("seed_demo")

PROTEGIDOS = {"puntorojo"}  # NUNCA sembrar datos demo sobre el tenant real.

DIAS_LV = [0, 1, 2, 3, 4]
FRANJAS = [("08:00", "12:00"), ("14:00", "18:00")]


@dataclass(frozen=True, slots=True)
class Cita:
    servicio: str
    recurso: str
    cliente: str
    telefono: str
    hora: tuple[int, int]      # (h, m) de HOY en Colombia
    dur_min: int
    estado: str = "confirmada"
    origen: str = "whatsapp"
    confirmacion: str = "reconfirmada"


@dataclass(frozen=True, slots=True)
class Hilo:
    telefono: str
    estado: str                # bot | humano
    motivo: str | None
    # (autor, texto, hace_horas) — autor cliente→entrante, bot/asesor→saliente.
    mensajes: list[tuple[str, str, float]]


@dataclass(frozen=True, slots=True)
class Vertical:
    slug: str
    nombre: str
    nit: str
    tema: str
    color: str
    nombre_comercial: str
    persona: str
    features: list[str]
    recursos: list[tuple[str, str]]              # (nombre, tipo)
    servicios: dict[str, tuple[int, int, str]]   # nombre → (dur_min, precio, categoria)
    asignaciones: dict[str, list[str]]
    citas_hoy: list[Cita]
    hilos: list[Hilo]
    n_citas_pasadas: int = 30                    # citas cumplidas en los últimos 30d (KPI "citas")
    n_encuestas: int = 50                        # respuestas de satisfacción (si pack_postventa)


# Reparto de calificaciones (1–5) → promedio ~4.6, realista para satisfacción.
_CALIF_DIST = [5] * 34 + [4] * 11 + [3] * 3 + [2] * 1 + [1] * 1


def _autor_dir(autor: str) -> str:
    return "entrante" if autor == "cliente" else "saliente"


# ── Verticales ────────────────────────────────────────────────────────────────
VERTICALES: dict[str, Vertical] = {
    "clinica-demo": Vertical(
        slug="clinica-demo", nombre="Clínica Sonría", nit="900900901",
        tema="aurora", color="#0e8784", nombre_comercial="Clínica Sonría",
        persona="Eres el asistente de la Clínica Sonría: cordial, claro y profesional. Confirmas servicio, fecha y nombre antes de agendar.",
        features=["pack_agenda", "canal_whatsapp", "pack_faq", "pack_postventa"],
        recursos=[("Dra. Pérez", "profesional"), ("Dr. Salas", "profesional")],
        servicios={"Limpieza dental": (40, 80000, "Odontología"), "Ortodoncia (control)": (30, 60000, "Ortodoncia"), "Blanqueamiento": (90, 220000, "Estética")},
        asignaciones={"Dra. Pérez": ["Limpieza dental", "Blanqueamiento"], "Dr. Salas": ["Ortodoncia (control)"]},
        citas_hoy=[
            Cita("Limpieza dental", "Dra. Pérez", "Carlos Martínez", "573001110001", (8, 0), 40, origen="dashboard"),
            Cita("Ortodoncia (control)", "Dr. Salas", "Luisa Ríos", "573001110002", (9, 0), 30, confirmacion="esperando"),
            Cita("Limpieza dental", "Dra. Pérez", "Pedro Pájaro", "573001110003", (10, 30), 40, estado="pendiente", confirmacion="esperando"),
            Cita("Ortodoncia (control)", "Dr. Salas", "Mariana Castro", "573001110004", (14, 0), 30, origen="dashboard", confirmacion="en_riesgo"),
            Cita("Blanqueamiento", "Dra. Pérez", "Andrés Malo", "573001110005", (15, 0), 90),
            Cita("Limpieza dental", "Dra. Pérez", "Karen Julio", "573001110006", (16, 30), 40, confirmacion="esperando"),
        ],
        hilos=[
            Hilo("573008124455", "humano", "Pide hablar con la doctora", [
                ("cliente", "Hola! Necesito el control de ortodoncia de este mes, ¿tienen algo mañana en la mañana?", 2),
                ("bot", "¡Hola! 😊 Claro. Para tu control (30 min con el Dr. Salas) mañana tengo 9:00, 10:00 y 11:30. ¿Cuál te queda mejor?", 2),
                ("cliente", "A las 9 está perfecto. Aunque quería preguntarle algo puntual a la doctora", 1),
                ("asesor", "Hola, soy la Dra. Pérez. Con gusto te ayudo, cuéntame.", 0.4),
            ]),
            Hilo("573001119988", "humano", "Plan prepagada", [
                ("cliente", "¿reciben Colsanitas prepagada?", 3),
                ("bot", "Déjame verificarlo con el equipo y te confirmo en un momento. 🙌", 3),
            ]),
            Hilo("573004445566", "bot", None, [
                ("cliente", "Quiero agendar una limpieza para el viernes", 5),
                ("bot", "Listo ✅ Tu limpieza dental quedó para el viernes 9:00am con la Dra. Pérez. Te enviaré un recordatorio.", 5),
            ]),
            Hilo("573002223344", "bot", None, [
                ("cliente", "¿a qué hora abren los sábados?", 7),
                ("bot", "Los sábados atendemos de 8:00am a 1:00pm. ¿Te ayudo a agendar? 😊", 7),
            ]),
        ],
    ),
    "restaurante-demo": Vertical(
        slug="restaurante-demo", nombre="Brasa & Leña", nit="900900902",
        tema="brasa", color="#d6452c", nombre_comercial="Brasa & Leña",
        persona="Eres el anfitrión virtual de Brasa & Leña: cálido y servicial. Tomas reservas y confirmas fecha, hora y número de personas.",
        features=["pack_agenda", "canal_whatsapp", "pack_faq", "pack_postventa"],
        recursos=[("Salón principal", "mesa"), ("Terraza", "mesa")],
        servicios={"Reserva mesa": (120, 0, "Reserva"), "Evento privado": (180, 0, "Evento")},
        asignaciones={"Salón principal": ["Reserva mesa", "Evento privado"], "Terraza": ["Reserva mesa"]},
        citas_hoy=[
            Cita("Reserva mesa", "Salón principal", "Familia Restrepo", "573001110011", (12, 30), 120, origen="dashboard"),
            Cita("Reserva mesa", "Terraza", "Diego Pardo", "573001110012", (13, 0), 120, confirmacion="esperando"),
            Cita("Reserva mesa", "Salón principal", "Ana Gómez", "573001110013", (19, 0), 120),
            Cita("Evento privado", "Salón principal", "Cumpleaños Sara", "573001110014", (20, 0), 180, origen="dashboard", confirmacion="en_riesgo"),
            Cita("Reserva mesa", "Terraza", "Luis Cano", "573001110015", (20, 30), 120, estado="pendiente", confirmacion="esperando"),
        ],
        hilos=[
            Hilo("573008124460", "humano", "Pide hablar con el encargado", [
                ("cliente", "Hola! Quería confirmar mi reserva para mañana 8pm, mesa para 4.", 2),
                ("bot", "¡Hola! 😊 Tengo tu reserva mañana 8:00pm, mesa para 4 en el salón. ¿Algo más?", 2),
                ("cliente", "Sí, quería pedir algo especial con el encargado por favor", 1),
                ("asesor", "Hola, soy el encargado. Con gusto, cuéntame qué necesitas.", 0.4),
            ]),
            Hilo("573001119977", "humano", "Opción sin gluten", [
                ("cliente", "¿tienen opción sin gluten para celíacos?", 3),
                ("bot", "Déjame confirmarlo con la cocina y te aviso enseguida. 🙌", 3),
            ]),
            Hilo("573004445577", "bot", None, [
                ("cliente", "Mesa para 2 esta noche a las 8?", 4),
                ("bot", "¡Listo! ✅ Reservé mesa para 2 hoy 8:00pm en la terraza. ¡Te esperamos! 🔥", 4),
            ]),
            Hilo("573002223355", "bot", None, [
                ("cliente", "¿hacen domicilios?", 6),
                ("bot", "Sí, hacemos domicilios de 12pm a 9pm. ¿Te paso el menú? 😋", 6),
            ]),
        ],
    ),
    "hotel-demo": Vertical(
        slug="hotel-demo", nombre="Hotel Brisa", nit="900900903",
        tema="brisa", color="#0b3954", nombre_comercial="Hotel Brisa",
        persona="Eres el conserje virtual del Hotel Brisa: elegante y atento. Gestionas reservas y confirmas fechas de entrada y salida.",
        features=["pack_agenda", "canal_whatsapp", "pack_postventa"],
        recursos=[("Habitación Vista Mar", "sala"), ("Suite Brisa", "sala")],
        servicios={"Estadía estándar": (120, 280000, "Alojamiento"), "Suite premium": (120, 520000, "Alojamiento")},
        asignaciones={"Habitación Vista Mar": ["Estadía estándar"], "Suite Brisa": ["Suite premium"]},
        citas_hoy=[
            Cita("Estadía estándar", "Habitación Vista Mar", "Sr. Beltrán", "573001110021", (14, 0), 120, origen="dashboard"),
            Cita("Suite premium", "Suite Brisa", "Familia Niebles", "573001110022", (15, 0), 120),
            Cita("Estadía estándar", "Habitación Vista Mar", "Laura Mejía", "573001110023", (16, 0), 120, confirmacion="esperando"),
            Cita("Suite premium", "Suite Brisa", "Sr. Vergara", "573001110024", (18, 0), 120, estado="pendiente", confirmacion="en_riesgo"),
        ],
        hilos=[
            Hilo("573008124470", "humano", "Pide late check-out", [
                ("cliente", "Buenas, llego mañana. ¿Es posible un late check-out el domingo?", 2),
                ("bot", "¡Con gusto verifico la disponibilidad de late check-out y le confirmo! 🌊", 2),
                ("cliente", "Gracias, y quería coordinar un traslado del aeropuerto", 1),
                ("asesor", "Buenas tardes, soy el conserje. Con gusto coordino su traslado.", 0.4),
            ]),
            Hilo("573001119966", "bot", None, [
                ("cliente", "¿Tienen disponibilidad para 2 noches este fin de semana?", 4),
                ("bot", "¡Sí! ✅ Tengo la Habitación Vista Mar para 2 noches este fin de semana. ¿Le reservo? 🌅", 4),
            ]),
            Hilo("573002223366", "bot", None, [
                ("cliente", "¿el desayuno está incluido?", 6),
                ("bot", "Sí, todas nuestras tarifas incluyen desayuno buffet de 7 a 10am. 🥐", 6),
            ]),
        ],
    ),
    "generico-demo": Vertical(
        slug="generico-demo", nombre="Estudio Lienzo", nit="900900904",
        tema="lienzo", color="#6c5ce7", nombre_comercial="Estudio Lienzo",
        persona="Eres el asistente de Estudio Lienzo: claro y resolutivo. Agendas citas y respondes dudas del servicio.",
        features=["pack_agenda", "canal_whatsapp", "pack_faq"],
        recursos=[("Consultor A", "profesional"), ("Sala Creativa", "sala")],
        servicios={"Sesión de consultoría": (60, 150000, "Consultoría"), "Taller": (120, 90000, "Taller")},
        asignaciones={"Consultor A": ["Sesión de consultoría"], "Sala Creativa": ["Taller"]},
        citas_hoy=[
            Cita("Sesión de consultoría", "Consultor A", "Marcela Ruiz", "573001110031", (9, 0), 60, origen="dashboard"),
            Cita("Taller", "Sala Creativa", "Equipo Norte", "573001110032", (11, 0), 120, confirmacion="esperando"),
            Cita("Sesión de consultoría", "Consultor A", "Julián Soto", "573001110033", (15, 0), 60),
            Cita("Sesión de consultoría", "Consultor A", "Paola Díaz", "573001110034", (16, 30), 60, estado="pendiente", confirmacion="esperando"),
        ],
        hilos=[
            Hilo("573008124480", "humano", "Cotización a medida", [
                ("cliente", "Hola, necesito una cotización para un taller de 20 personas", 2),
                ("bot", "¡Hola! Con gusto. Para un taller de 20 personas preparo una propuesta y te la comparto. 📋", 2),
                ("cliente", "Perfecto, ¿puedo hablar con alguien del equipo?", 1),
                ("asesor", "Hola, soy del equipo de Lienzo. Con gusto armamos la propuesta juntos.", 0.4),
            ]),
            Hilo("573001119955", "bot", None, [
                ("cliente", "Quiero una sesión de consultoría el jueves", 4),
                ("bot", "¡Listo! ✅ Agendé tu consultoría el jueves 3:00pm con el Consultor A. 🗓️", 4),
            ]),
            Hilo("573002223377", "bot", None, [
                ("cliente", "¿hacen sesiones virtuales?", 6),
                ("bot", "¡Sí! Ofrecemos sesiones presenciales y virtuales. ¿Cuál prefieres? 💻", 6),
            ]),
        ],
    ),
    "barberia-demo": Vertical(
        slug="barberia-demo", nombre="Barbería Navaja", nit="900900905",
        tema="navaja", color="#d99a3d", nombre_comercial="Barbería Navaja",
        persona="Eres el asistente de Barbería Navaja: relajado y cercano. Agendas cortes y confirmas barbero, servicio y hora.",
        features=["pack_agenda", "canal_whatsapp", "pack_postventa"],
        recursos=[("Tijeras (barbero)", "profesional"), ("Bigote (barbero)", "profesional")],
        servicios={"Corte clásico": (30, 25000, "Corte"), "Corte + barba": (45, 38000, "Corte"), "Afeitado a navaja": (30, 28000, "Barba")},
        asignaciones={"Tijeras (barbero)": ["Corte clásico", "Corte + barba"], "Bigote (barbero)": ["Afeitado a navaja", "Corte + barba"]},
        citas_hoy=[
            Cita("Corte + barba", "Tijeras (barbero)", "Camilo R.", "573001110041", (9, 0), 45, origen="dashboard"),
            Cita("Corte clásico", "Tijeras (barbero)", "Sebastián G.", "573001110042", (10, 0), 30, confirmacion="esperando"),
            Cita("Afeitado a navaja", "Bigote (barbero)", "Mateo L.", "573001110043", (11, 30), 30),
            Cita("Corte clásico", "Tijeras (barbero)", "Andrés P.", "573001110044", (15, 0), 30, origen="dashboard", confirmacion="en_riesgo"),
            Cita("Corte + barba", "Bigote (barbero)", "Felipe O.", "573001110045", (16, 0), 45, estado="pendiente", confirmacion="esperando"),
        ],
        hilos=[
            Hilo("573008124490", "humano", "Pide cita urgente", [
                ("cliente", "Parce, necesito un corte hoy mismo, ¿hay campo?", 2),
                ("bot", "¡Qué más! 💈 Déjame revisar la agenda de hoy y te digo enseguida.", 2),
                ("cliente", "Listo, y de una el de barba también", 1),
                ("asesor", "Hola, soy de la barbería. Te tengo a las 3pm con corte + barba, ¿te sirve?", 0.4),
            ]),
            Hilo("573001119944", "bot", None, [
                ("cliente", "Quiero agendar corte clásico para el sábado", 4),
                ("bot", "¡Listo! ✅ Te agendé el corte clásico el sábado 10:00am con Tijeras. 💈", 4),
            ]),
            Hilo("573002223388", "bot", None, [
                ("cliente", "¿cuánto vale el afeitado a navaja?", 6),
                ("bot", "El afeitado a navaja está en $28.000 e incluye toalla caliente. 🪒 ¿Te agendo?", 6),
            ]),
        ],
    ),
}


# ── Catálogo (get-or-create, idempotente) ────────────────────────────────────
def _id_por_nombre(conn, tabla: str, nombre: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {tabla} WHERE nombre = %s", (nombre,)).fetchone()
    return row["id"] if row else None


def _seed_catalogo(conn, v: Vertical) -> tuple[dict[str, int], dict[str, int]]:
    servicios: dict[str, int] = {}
    for nombre, (dur, precio, cat) in v.servicios.items():
        existente = _id_por_nombre(conn, "servicios", nombre)
        servicios[nombre] = existente or conn.execute(
            "INSERT INTO servicios (nombre, duracion_min, precio, categoria, activo) "
            "VALUES (%s,%s,%s,%s,true) RETURNING id",
            (nombre, dur, precio, cat),
        ).fetchone()["id"]
    recursos: dict[str, int] = {}
    for nombre, tipo in v.recursos:
        existente = _id_por_nombre(conn, "recursos", nombre)
        recursos[nombre] = existente or conn.execute(
            "INSERT INTO recursos (nombre, tipo, activo) VALUES (%s,%s::recurso_tipo,true) RETURNING id",
            (nombre, tipo),
        ).fetchone()["id"]
    for recurso_nombre, lista in v.asignaciones.items():
        for servicio_nombre in lista:
            conn.execute(
                "INSERT INTO recurso_servicio (recurso_id, servicio_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                (recursos[recurso_nombre], servicios[servicio_nombre]),
            )
    for recurso_id in recursos.values():
        for dia in DIAS_LV:
            for hi, hf in FRANJAS:
                ya = conn.execute(
                    "SELECT 1 FROM disponibilidad WHERE recurso_id=%s AND dia_semana=%s AND hora_inicio=%s AND hora_fin=%s",
                    (recurso_id, dia, hi, hf),
                ).fetchone()
                if ya is None:
                    conn.execute(
                        "INSERT INTO disponibilidad (recurso_id, dia_semana, hora_inicio, hora_fin) VALUES (%s,%s,%s,%s)",
                        (recurso_id, dia, hi, hf),
                    )
    conn.execute(
        "INSERT INTO agenda_config (id, modo_confirmacion, persona) VALUES (1, 'auto'::modo_confirmacion, %s) "
        "ON CONFLICT (id) DO UPDATE SET persona=EXCLUDED.persona, actualizado_en=now()",
        (v.persona,),
    )
    return servicios, recursos


# ── Showcase (borrar + re-sembrar) ────────────────────────────────────────────
def _hoy_a(h: int, m: int) -> datetime:
    return datetime.combine(today_co(), time(h, m), tzinfo=COLOMBIA_TZ)


def _seed_citas(conn, v: Vertical, servicios: dict[str, int], recursos: dict[str, int]) -> int:
    conn.execute("DELETE FROM citas")
    n = 0
    for c in v.citas_hoy:
        inicio = _hoy_a(*c.hora)
        fin = inicio + timedelta(minutes=c.dur_min)
        conn.execute(
            "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, inicio, fin, "
            "estado, origen, confirmacion) VALUES (%s,%s,%s,%s,%s,%s,%s::cita_estado,%s::cita_origen,%s::cita_confirmacion)",
            (servicios[c.servicio], recursos[c.recurso], c.cliente, c.telefono, inicio, fin,
             c.estado, c.origen, c.confirmacion),
        )
        n += 1
    # Citas pasadas CUMPLIDAS (últimos 30d) para el KPI "citas" — reparten servicios/recursos en round-robin.
    serv_ids = list(servicios.values())
    rec_pairs = list(v.asignaciones.items())
    base = now_co()
    for i in range(v.n_citas_pasadas):
        dia = base - timedelta(days=1 + (i % 29), hours=(i % 6))
        rec_nombre, servs = rec_pairs[i % len(rec_pairs)]
        serv_nombre = servs[i % len(servs)]
        inicio = dia.replace(minute=0, second=0, microsecond=0)
        conn.execute(
            "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, inicio, fin, "
            "estado, origen, confirmacion) VALUES (%s,%s,%s,%s,%s,%s,'cumplida','whatsapp','reconfirmada')",
            (servicios[serv_nombre], recursos[rec_nombre], f"Cliente {i+1}", f"57300999{i:04d}",
             inicio, inicio + timedelta(minutes=40)),
        )
        n += 1
    return n


def _seed_conversaciones(conn, v: Vertical) -> tuple[int, int]:
    conn.execute("DELETE FROM conversacion_mensajes")
    conn.execute("DELETE FROM conversaciones")
    ahora = now_co()
    n_conv = n_msg = 0
    for h in v.hilos:
        creada = ahora - timedelta(hours=8)
        escalada = ahora - timedelta(hours=1) if h.estado == "humano" else None
        conn.execute(
            "INSERT INTO conversaciones (cliente_telefono, estado, motivo, creada_en, escalada_en) "
            "VALUES (%s,%s::conversacion_estado,%s,%s,%s)",
            (h.telefono, h.estado, h.motivo, creada, escalada),
        )
        n_conv += 1
        for autor, texto, hace_h in h.mensajes:
            conn.execute(
                "INSERT INTO conversacion_mensajes (cliente_telefono, direccion, autor, texto, creada_en) "
                "VALUES (%s,%s::mensaje_direccion,%s::mensaje_autor,%s,%s)",
                (h.telefono, _autor_dir(autor), autor, texto, ahora - timedelta(hours=hace_h)),
            )
            n_msg += 1
    return n_conv, n_msg


def _seed_encuestas(conn, v: Vertical) -> int:
    conn.execute("DELETE FROM encuestas_respuestas")
    if "pack_postventa" not in v.features:
        return 0
    base = now_co()
    for i in range(v.n_encuestas):
        calif = _CALIF_DIST[i % len(_CALIF_DIST)]
        creado = base - timedelta(days=(i % 29), hours=(i % 12))
        conn.execute(
            "INSERT INTO encuestas_respuestas (telefono, calificacion, creado_en) VALUES (%s,%s,%s)",
            (f"57300888{i:04d}", calif, creado),
        )
    return v.n_encuestas


def seed_showcase(conn_url: str, v: Vertical) -> dict[str, int]:
    """Siembra catálogo + datos de showcase en la BD del tenant demo. Idempotente. NUNCA Punto Rojo."""
    if v.slug in PROTEGIDOS:
        raise ValueError(f"seed_demo: rehúso sembrar datos demo sobre el tenant protegido '{v.slug}'")
    with psycopg.connect(to_libpq(conn_url), row_factory=dict_row) as conn:
        servicios, recursos = _seed_catalogo(conn, v)
        n_citas = _seed_citas(conn, v, servicios, recursos)
        n_conv, n_msg = _seed_conversaciones(conn, v)
        n_enc = _seed_encuestas(conn, v)
        conn.commit()
    resumen = {"citas": n_citas, "conversaciones": n_conv, "mensajes": n_msg, "encuestas": n_enc}
    log.info("demo_showcase_sembrado", slug=v.slug, **resumen)
    return resumen


def provision_demo(v: Vertical) -> dict[str, int]:
    """Aprovisiona el tenant demo (DB + features + branding/tema) y siembra su showcase. Idempotente."""
    if v.slug in PROTEGIDOS:
        raise ValueError(f"seed_demo: '{v.slug}' está protegido")
    settings = get_settings()
    empresa_id = provision_tenant(v.slug, v.nombre, v.nit, admin_nombre="Admin Demo")
    cargar_plan_features(empresa_id, {"features_override": {f: True for f in v.features}})
    cargar_secretos_empresa(empresa_id, {"branding": {
        "nombre_comercial": v.nombre_comercial, "color_primario": v.color, "tema": v.tema,
    }})
    conn_url = tenant_url(settings.tenants_direct_url_base, f"ferrebot_{v.slug}")
    resumen = seed_showcase(conn_url, v)
    log.info("demo_provisionado", slug=v.slug, empresa_id=empresa_id, tema=v.tema)
    return resumen


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    configure_logging()
    parser = argparse.ArgumentParser(description="Sembrar tenants demo por vertical (modo demo, Fase 3c).")
    parser.add_argument("--only", help="Sembrar solo este slug (p. ej. barberia-demo)")
    args = parser.parse_args(argv)

    objetivos = [VERTICALES[args.only]] if args.only else list(VERTICALES.values())
    if args.only and args.only not in VERTICALES:
        parser.error(f"slug demo desconocido: {args.only} (opciones: {', '.join(VERTICALES)})")

    for v in objetivos:
        r = provision_demo(v)
        print(f"✅ {v.slug} ({v.tema}) — citas={r['citas']} conversaciones={r['conversaciones']} "
              f"mensajes={r['mensajes']} encuestas={r['encuestas']}")
    print(f"\nListo: {len(objetivos)} tenant(s) demo sembrados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
