# ADR 0007 — Manifiesto de tenant + provisionador idempotente de un paso

> Estado: **Propuesto** (8 jun 2026). Decide el formato y el contrato para que dar de alta un
> cliente real sea **escribir un archivo + correr un comando**, sin programar nada por tenant.
> Contexto y motivación en `docs/whatsapp-agentes-arquitectura.md` (runtime + packs + datos por tenant).

## Contexto

Hoy onboardear un **tenant-agente** (servicios: clínica, spa, beach club) NO es declarativo. La
secuencia real, medida contra el código:

| Paso | Cómo se hace hoy | ¿Declarativo? |
|---|---|---|
| Base + migrar + admin + secretos + branding + plan/features | `tools/provision_tenant.py --from <json>` | ✅ pero el JSON tiene forma **fiscal/POS** (matias, cloudinary, telegram_token) |
| Prender packs (`pack_agenda`, `canal_whatsapp`, `pack_faq`) | `features_override` o `tools/set_feature.py` | ✅ parcial |
| Datos de agenda (servicios, recursos, disponibilidad, asignaciones, `agenda_config`, persona) | **`tools/seed_clinica_demo.py` (bespoke, hardcodeado)** | ❌ **hueco principal** |
| Entradas de FAQ (`conocimiento`) | a mano (CRUD del dashboard) | ❌ |
| Mapear número de WhatsApp (`wa_numeros`) | `tools/seed_wa_numero.py <phone_number_id> <slug>` | ❌ comando manual |

**No existe ningún loader genérico que lea un manifiesto.** Para el cliente #2 se copia y edita
`seed_clinica_demo.py` a mano: ese es el caos que este ADR elimina.

## Decisión

Un **manifiesto de tenant** (un archivo declarativo por empresa) + un **provisionador idempotente de
un paso** `provision_from_manifest(<archivo>)` que ejecuta toda la coreografía y se puede reintentar
sin romper nada.

### D1 — Formato: **YAML** (el loader también acepta JSON)

`yaml.safe_load` parsea YAML **y** JSON (JSON es subconjunto de YAML) → un único loader, retrocompatible
con los `tools/onboarding/*.json` actuales. Se elige YAML para autoría porque el manifiesto crece
(agenda + disponibilidad + FAQ) y YAML admite comentarios y es mantenible por un humano. `pyyaml` es
dependencia trivial. *Rechazado:* JSON puro (verboso, sin comentarios, hostil para editar a mano).

### D2 — **Un manifiesto por tenant, seccionado por pack** (no overlays)

Una sola fuente de verdad por empresa: greppable, versionable, idempotente. Internamente se organiza en
secciones (`identidad`, `plan`, `branding`, `packs.<nombre>`, `canal`) → la data de cada pack es un
bloque autocontenido, lo que deja la puerta abierta a dividir en overlays más adelante **sin cambiar el
esquema**. *Rechazado por ahora:* base + overlays por pack (composición innecesaria para N < 20 tenants).

### D3 — Secretos: igual que hoy (cifrados en control DB; el manifiesto real va gitignored)

El bloque `secretos` es **opcional**: un agente de servicios por Kapso puede tener **cero secretos
por-tenant** (`KAPSO_API_KEY` es de plataforma, en env; el `phone_number_id` no es secreto). Si hay
secretos (p. ej. MATIAS de una ferretería), el provisionador los cifra en `secretos_empresa` con
`SECRETS_MASTER_KEY`, como ya hace `provision_tenant`. Los manifiestos con valores reales viven bajo
`tools/onboarding/` y están **gitignored**; solo se versiona el `*.example.yaml`.

### D4 — **Registro de packs declarativo** (el *seam* que el panel futuro togglea)

Cada pack se declara una vez en un registro (`tools/manifest/registry.py`), con:

```
Pack(
  flag="pack_agenda",                 # feature del catálogo (core/tenancy/catalogo.py)
  loader=cargar_agenda,               # (manifiesto["packs"]["agenda"], conn) -> upsert idempotente
  tablas=("servicios","recursos","recurso_servicio","disponibilidad","agenda_config"),
)
```

El provisionador itera **solo los packs activos** (según `features` efectivas) y corre su `loader`.
Añadir un vertical nuevo = registrar un pack + su loader, sin tocar el orquestador. Esto materializa la
capa "capability packs" de la arquitectura y es exactamente el catálogo que un panel super-admin
encenderá por flag.

### D5 — Contrato del provisionador (idempotente end-to-end)

`provision_from_manifest(path: str) -> int  # devuelve empresa_id`

1. **Parsear + validar** el manifiesto (Pydantic): forma del esquema, features contra
   `core/tenancy/catalogo.py`, dependencias del set **efectivo** (`validar_dependencias`). **Falla
   cerrado**: si algo no valida, **no escribe nada**.
2. **Base** — reusa la maquinaria de `provision_tenant`: `CREATE DATABASE` → registrar en control (URL
   cifrada) → `upgrade head` (tenant) → admin → secretos/config/branding → plan/`empresa_features`.
3. **Loaders de packs activos** — por cada pack activo en el manifiesto, su loader hace **upsert**
   idempotente sobre la BD del tenant (resuelve nombres→ids; ej.: `recurso.presta` → `recurso_servicio`).
4. **Canal** — upsert en `wa_numeros` (`phone_number_id` único) si hay `canal.whatsapp`.
5. **Verificar** — smoke read-only (conteos por tabla) + resumen impreso
   (`provision_manifest: clinica-demo OK → 3 servicios, 2 recursos, 4 faq, wa: 117676…`).

Driver **sync (psycopg)** como el resto de tools de provisioning (no toca el caché de engines async de
la API). Re-ejecutar actualiza, no duplica (UPSERT por claves naturales).

### D6 — Validación de aceptación

Reconstruir **clinica-demo desde un manifiesto** y probar que los loaders producen **filas idénticas**
a las del `seed_clinica_demo` bespoke (conteos + campos clave). Cuando pase, **deprecar
`seed_clinica_demo.py`**. Esa prueba es la definición de "el onboarding ya es declarativo".

## Consecuencias

**A favor:** onboardear un cliente real = escribir un YAML + un comando; los datos de pack dejan de ser
código bespoke; el registro de packs es la base directa del panel super-admin futuro; idempotencia hace
el alta reintentable y segura en prod (vía `railway ssh`).

**En contra / costo:** nueva dependencia `pyyaml`; hay que mantener el esquema del manifiesto en sync con
los modelos de cada pack (mitigado: validación Pydantic + la prueba de aceptación que rompe si divergen);
el `provision_tenant` actual se refactoriza para exponer sus piezas reutilizables (la base) sin duplicar.

**Fuera de alcance (frente siguiente, no este ADR):** **login real** (email/contraseña) y **panel
super-admin self-serve**. Hoy el acceso al dashboard es por `dev_token`; un cliente de servicios puede
operar desde WhatsApp + Google Calendar + Inbox de Kapso sin dashboard. El login real solo bloquea
cuando el cliente edita su propia data; se aborda cuando ese sea el caso (ver
`docs/plan-provisionador-manifiesto.md` §Fase 5).

## Alternativas consideradas

- **Seguir con seeds bespoke por tenant** — rechazado: no escala, copia-pega frágil, es el problema.
- **Construir primero el panel web** — rechazado: el panel no es más que un formulario que produce este
  manifiesto y llama a este provisionador; construirlo antes sería al revés.
- **Login real + self-serve ahora** — diferido: no bloquea al primer cliente real asistido; ver §D6/§Consecuencias.
