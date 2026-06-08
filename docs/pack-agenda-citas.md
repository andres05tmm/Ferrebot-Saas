# Capability pack: Agenda / Citas

> Diseño del **primer pack de acción** de la plataforma de agentes de WhatsApp (7 jun 2026).
> Contexto: ver `whatsapp-agentes-arquitectura.md` (runtime genérico + packs modulares + datos por
> tenant). Este pack cubre negocios **de servicio por cita**: odontología, consultorios médicos,
> peluquería/barbería, spa, estética/uñas, veterinaria, fisioterapia, asesorías. Un solo motor de
> agenda sirve a todos esos verticales — por eso "encasilla a muchas empresas".
>
> **No es código todavía:** es el esquema de reglas, el motor, las herramientas y las decisiones abiertas.

---

## Qué resuelve

Un cliente final escribe por WhatsApp ("quiero una limpieza el viernes en la tarde") y el agente:
agenda, reagenda, cancela o consulta sus citas — 24/7, sin que el negocio teclee nada. Lo no agendable
(dudas) lo responde el pack **FAQ** o lo escala a un humano.

Recordatorio del principio (ver arquitectura): **reglas en datos, cómo se computan en el motor, y el
agente nunca calcula** — solo entiende la intención y llama a la herramienta correcta.

---

## Capa 1 — Datos configurables (lo que cada negocio "nutre")

Viven en la **base del propio tenant** (aislamiento por construcción). Negocio nuevo = llenar estos
valores, sin programar.

### `servicios` — qué se puede agendar
| Campo | Tipo | Nota |
|---|---|---|
| `id` | int | |
| `nombre` | str | "Limpieza dental", "Corte de cabello" |
| `duracion_min` | int | minutos que ocupa la cita |
| `precio` | numeric | opcional (puede ir solo informativo) |
| `buffer_antes_min` | int | tiempo muerto antes (preparación) — default 0 |
| `buffer_despues_min` | int | tiempo muerto después (limpieza) — default 0 |
| `categoria` | str | opcional, para agrupar |
| `descripcion` | str | opcional, alimenta también el FAQ |
| `activo` | bool | |

### `recursos` — quién/qué presta el servicio
Generalizamos a **recurso** (no solo "profesional") para que el mismo pack sirva a peluquería (estilista),
clínica (doctor o silla), veterinaria (consultorio), o más adelante canchas/salas. (← decisión abierta #1.)

| Campo | Tipo | Nota |
|---|---|---|
| `id` | int | |
| `nombre` | str | "Dra. Pérez", "Silla 2", "Consultorio A" |
| `tipo` | str | `profesional` \| `sala` \| `equipo` |
| `activo` | bool | |

### `recurso_servicio` — qué recurso presta qué servicio (N:N)
`recurso_id`, `servicio_id`. (Si el negocio tiene un solo recurso, se autollena.)

### `disponibilidad` — horario semanal de cada recurso
| Campo | Tipo | Nota |
|---|---|---|
| `recurso_id` | int | |
| `dia_semana` | int | 0=lunes … 6=domingo |
| `hora_inicio` | time | |
| `hora_fin` | time | varias filas por día → mañana y tarde |

### `bloqueos` — excepciones (ausencias, festivos, citas externas)
| Campo | Tipo | Nota |
|---|---|---|
| `recurso_id` | int \| null | null = bloqueo global del negocio |
| `inicio` | datetime | |
| `fin` | datetime | |
| `motivo` | str | opcional |

### `agenda_config` — reglas globales del negocio (una fila por tenant)
| Campo | Default | Qué controla |
|---|---|---|
| `zona_horaria` | `America/Bogota` | siempre Colombia |
| `intervalo_slots_min` | 15 | granularidad de los cupos ofrecidos |
| `anticipacion_minima_min` | 120 | no agendar con menos de X min de anticipación |
| `ventana_maxima_dias` | 30 | hasta cuántos días hacia adelante se puede agendar |
| `politica_cancelacion_horas` | 24 | mínimo para cancelar/reagendar sin fricción |
| `permite_reagendar` | true | |
| `modo_confirmacion` | `auto` | `auto` = confirmada al instante; `manual` = queda `pendiente` hasta que el negocio apruebe |
| `requiere_anticipo` | false | si true, exige pago de adelanto para confirmar (hoteles, servicios caros) |
| `anticipo_tipo` / `anticipo_valor` | — | `porcentaje` \| `fijo` + monto; el **cobro real depende del frente de pagos** (Bre-B/link) |
| `capacidad_por_slot` | 1 | >1 habilita citas de grupo (clases) |
| `recordatorios_horas` | `[24, 2]` | cuándo enviar recordatorio (plantilla) antes de la cita |
| `persona` | — | tono/saludo del agente para ese negocio |

---

## Capa 2 — El motor (código del pack, determinista, igual para todos)

- **`calcular_disponibilidad(servicio, fecha|rango, recurso?) -> [slots]`**
  Toma el horario del/los recurso(s) que prestan el servicio, **resta** citas existentes y `bloqueos`,
  respeta `duracion_min` + buffers, parte en cupos de `intervalo_slots_min`, y descarta lo que viole
  `anticipacion_minima_min` / `ventana_maxima_dias` / `capacidad_por_slot`. Devuelve cupos libres.
- **`validar_y_agendar(...) -> cita | error`**
  Revisa reglas y **toma un lock** sobre el cupo antes de insertar (dos personas pidiendo el mismo cupo →
  solo una gana; la otra recibe alternativas). Idempotente por `idempotency_key`.
- **`reagendar(cita, nuevo_slot)` / `cancelar(cita)`** — aplican `politica_cancelacion_horas`.
- **Job de recordatorios** (worker ARQ existente): a `recordatorios_horas` antes, envía plantilla de
  WhatsApp. *(Son mensajes iniciados por el negocio → plantilla **paga**, fuera de la ventana de 24h.)*

### `citas` — transaccional (no es config; lo genera el motor)
| Campo | Nota |
|---|---|
| `id` | |
| `servicio_id`, `recurso_id` | |
| `cliente_nombre`, `cliente_telefono` | el teléfono = identidad del cliente (su número de WhatsApp) |
| `inicio`, `fin` | datetime con zona Colombia |
| `estado` | `pendiente` → `confirmada` → `cumplida` \| `cancelada` \| `no_show` |
| `origen` | `whatsapp` \| `dashboard` |
| `notas` | opcional |
| `idempotency_key` | evita doble inserción |
| `creada_en` | |

---

## Capa 3 — Herramientas del agente (function-calling, de cara al cliente, SEGURAS)

El agente solo orquesta; cada herramienta llama al motor. Toda acción sobre citas está **acotada al
número de WhatsApp del cliente** que escribe.

| Herramienta | Qué hace | Guardarraíl |
|---|---|---|
| `listar_servicios()` | nombre, duración, precio | solo activos |
| `consultar_disponibilidad(servicio, fecha\|rango, recurso?)` | cupos libres | llama al motor, nunca calcula el agente |
| `agendar_cita(servicio, slot, nombre, recurso?)` | crea la cita | lock anti-doble-reserva; respeta reglas |
| `mis_citas()` | próximas citas del cliente | **solo las del teléfono que escribe** |
| `reagendar_cita(ref, nuevo_slot)` | mueve la cita | solo citas propias; aplica política |
| `cancelar_cita(ref)` | cancela | solo citas propias; aplica política |
| `responder_faq(pregunta)` | dudas (horario, ubicación, precios) | pack FAQ (RAG sobre info del negocio) |
| `escalar_humano(motivo)` | pasa a un humano | crea aviso en dashboard / notifica |

**Lo que el agente NUNCA puede hacer** (de cara al cliente): ver o tocar citas de **otros** clientes,
borrar datos del negocio, cambiar precios/horarios, ni operaciones destructivas. Solo lo de su rol.

---

## Cómo se "nutre" una clínica nueva (onboarding del pack)

1. Activar flags `canal_whatsapp` + `pack_agenda` (+ `pack_faq`).
2. Cargar `servicios` (con duración), `recursos` y su `disponibilidad`.
3. Fijar `agenda_config` (anticipación, ventana, política de cancelación, recordatorios, persona).
4. (Opcional) Subir info para el FAQ (ubicación, parqueo, formas de pago…).

Eso es todo. El segundo odontólogo es **solo configuración**; el primero fue construir el pack.

---

## Ejemplo de conversación

```
Cliente:  Hola, quiero una limpieza dental esta semana en la tarde
Agente:   [listar_servicios] [consultar_disponibilidad("Limpieza", "esta semana tarde")]
          Tenemos "Limpieza dental" (40 min, $80.000). Disponible:
          jueves 3:00 pm, jueves 4:30 pm, viernes 2:00 pm. ¿Cuál te sirve?
Cliente:  El viernes a las 2
Agente:   [agendar_cita("Limpieza", "vie 14:00", "—")]  ¿A nombre de quién?
Cliente:  Andrés Malo
Agente:   Listo Andrés ✅ Limpieza dental, viernes 2:00 pm con la Dra. Pérez.
          Te recordamos un día antes. ¿Algo más?
```

---

## Cómo encaja con el stack actual
- **DB por tenant**: todas estas tablas viven en la base de cada negocio (aislamiento por construcción).
- **Zona horaria Colombia** (`COLOMBIA_TZ`) en todo cálculo de cupos — regla crítica del proyecto.
- **Worker ARQ/Redis** ya existe → recordatorios como job.
- **Feature flags** → `pack_agenda` enciende tablas/herramientas/tab del dashboard.
- **Dashboard**: el negocio ve/edita sus citas y atiende los `escalar_humano` (calendario + bandeja).

---

## Decisiones abiertas (a discutir antes de implementar)

1. ~~¿`recurso` genérico o solo `profesional`?~~ → **DECIDIDO (7 jun 2026): genérico** (`recursos` con
   `tipo` = profesional/sala/equipo/mesa/cancha). Mismo motor para cualquier cosa reservable; en la UI/al
   cliente se muestra según el tipo. Encasilla muchos más verticales sin tocar el motor.
2. ~~¿Autoconfirma o el negocio confirma?~~ → **DECIDIDO (7 jun 2026):** configurable por negocio
   (`modo_confirmacion` = `auto` | `manual`), default `auto`. Se ofrecen las dos y el negocio elige.
3. ~~¿Anticipo/pago al reservar?~~ → **DECIDIDO:** configurable (`requiere_anticipo`); algunos negocios
   (hoteles, servicios caros) lo exigen. **El campo se diseña ahora**, pero el **cobro real se activa
   cuando exista el frente de pagos** (Bre-B/link). Hasta entonces, `manual` + aviso de "pendiente de pago".
4. **¿Citas de grupo (capacidad > 1)?** Dejar la columna `capacidad_por_slot` lista, pero v1 puede asumir 1.
5. ~~¿Sincronizar con Google Calendar?~~ → **DECIDIDO (7 jun 2026):** sí, como **sync opcional por
   tenant, write-only**, con **service account** (no OAuth). El negocio comparte su calendario con el
   email del SA y guarda solo su `google_calendar_id`; al agendar/reagendar/cancelar se espeja el
   evento best-effort (si Google falla, la cita no falla). Leer disponibilidad de Google = futuro. Ver
   `docs/adr/0006-agenda-google-calendar-sync.md`.
6. **Recordatorios = plantillas pagas.** Confirmar que el costo (mensaje proactivo) lo asume el negocio.
7. **Identidad del cliente por número de WhatsApp.** Caso borde: alguien agenda a nombre de otro → v2.
8. **Habeas Data (Ley 1581):** guardamos mínimos (nombre, teléfono); definir retención/baja al sumar
   empresas externas.

---

## Siguiente paso sugerido
Cuando cerremos las decisiones (sobre todo #1 y #2), el primer prompt para Claude Code sería la
**migración + modelos del pack** (las tablas de arriba como módulo `modules/agenda/`), seguido del
**motor de disponibilidad** con sus tests, y luego las **herramientas** conectadas al runtime del agente.
