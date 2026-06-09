# Plan — Manifiesto de tenant + provisionador de un paso (prompts para Claude Code)

> Acompaña al **ADR 0007** (`docs/adr/0007-manifiesto-tenant-y-provisionador.md`) y al manifiesto de
> ejemplo (`tools/onboarding/clinica-demo.manifest.example.yaml`).
>
> **Cómo se usa este doc:** cada fase trae un **prompt pegable** para Claude Code. Andrés lo pega, Claude
> Code escribe el código (TDD, según `.claude/rules/`), Andrés revisa el diff y corre `pytest`. Las fases
> son secuenciales (cada una depende de la anterior). **No saltarse la verificación de cada fase.**

## Objetivo

Que dar de alta un cliente real = **escribir un manifiesto YAML + correr un comando idempotente**, sin
programar nada por tenant. Prueba de que se logró: clinica-demo se reconstruye desde un manifiesto y
produce filas idénticas al seed bespoke actual (Fase 4).

## Mapa de fases

| Fase | Entrega | Depende de |
|---|---|---|
| 0 | ADR + manifiesto de ejemplo (este commit, hecho por Cowork) | — |
| 1 | Parser + validación del manifiesto (Pydantic, sin BD) | 0 |
| 2 | Registro de packs + loaders idempotentes (agenda, faq) | 1 |
| 3 | `provision_from_manifest` (orquestador de un paso) + canal | 2 |
| 4 | Validación de aceptación (reconstruir clinica-demo) + deprecar seed bespoke | 3 |
| 5 | *(diferida)* login real + panel super-admin self-serve | 4 |

Reglas transversales que Claude Code debe respetar en TODAS las fases (de `.claude/rules/`): TDD (test
RED→GREEN→refactor); idempotencia en lo crítico; **zona horaria Colombia** (`now_co`/`COLOMBIA_TZ`,
nunca `date.today()`); logging estructurado con `tenant_id` (nunca `print` salvo el resumen final de un
tool CLI); driver **sync psycopg** en tools de provisioning (no tocar el caché de engines async);
validar features contra `core/tenancy/catalogo.py`.

---

## Fase 1 — Parser + validación del manifiesto

**Meta:** cargar el YAML a modelos Pydantic tipados y validarlo **sin tocar la BD**. Falla cerrado.

```
Contexto: implementamos el ADR docs/adr/0007 (manifiesto de tenant + provisionador idempotente).
Esquema de referencia: tools/onboarding/clinica-demo.manifest.example.yaml. Reglas: .claude/rules/.

Tarea (Fase 1, solo parsing/validación, SIN BD): crea el paquete tools/manifest/ con:
- schema.py: modelos Pydantic v2 que tipan el manifiesto del ejemplo (version, identidad{slug,nombre,nit},
  admin, plan{nombre,features}, features_override, branding, secretos, config, packs{agenda,faq}, canal).
  Para packs.agenda: config (mapea a agenda_config), servicios[], recursos[] (con presta[] y
  disponibilidad[{dias[],franjas[]}]). Para packs.faq: entradas[]. Para canal.whatsapp:
  phone_number_id/numero/waba_id. Campos opcionales con defaults sensatos; secretos/config opcionales.
- loader.py: cargar_manifiesto(path) -> Manifiesto, usando yaml.safe_load (acepta YAML y JSON).
  Añade pyyaml a pyproject.toml [project.dependencies].
- validacion.py: validar(manifiesto) que comprueba, REUSANDO core/tenancy/catalogo.py: (a) toda feature
  de plan.features/features_override existe (es_feature_valida); (b) las dependencias del set EFECTIVO se
  cumplen (validar_dependencias sobre capacidades_completas). Reusa la lógica de _features_efectivas de
  tools/provision_tenant.py (no la dupliques: extráela a un sitio común si hace falta). Valida también
  que las franjas de disponibilidad sean "HH:MM-HH:MM" y dias en 0..6, y que cada recurso.presta
  referencie un servicio declarado. Error claro y NO escribe nada si algo falla.

TDD: tests en tests/ que cubran: manifiesto válido (el ejemplo) parsea OK; feature inexistente -> error;
dependencia faltante (p.ej. libro_iva sin facturacion_electronica) -> error; presta a servicio
inexistente -> error; franja mal formada -> error. Función pura, sin red ni BD. Corre pytest.
```

**Verificación Fase 1:** `pytest tests/ -k manifest` verde; cargar el ejemplo no lanza; un manifiesto
con feature basura o dependencia rota falla con mensaje claro.

---

## Fase 2 — Registro de packs + loaders idempotentes

**Meta:** reemplazar el seed bespoke por loaders genéricos que leen el manifiesto y hacen upsert. Aquí
vive la reusabilidad.

```
Contexto: Fase 2 del ADR docs/adr/0007. Ya existe tools/manifest/{schema,loader,validacion}.py (Fase 1).
Hoy tools/seed_clinica_demo.py siembra agenda con SQL bespoke (lee su patrón: servicios, recursos,
recurso_servicio, disponibilidad, agenda_config). Tablas y columnas reales en modules/agenda/models.py
y modules/faq/models.py.

Tarea: crea tools/manifest/packs/ con loaders idempotentes (driver sync psycopg, como provision_tenant):
- agenda.py: cargar_agenda(seccion_agenda, conn) que upserta, en este orden, sobre la BD del tenant:
  servicios (por nombre), recursos (por nombre, tipo::recurso_tipo), recurso_servicio (resolviendo
  recurso.presta nombre->id; ON CONFLICT DO NOTHING), disponibilidad (por recurso_id+dia+franja, sin
  duplicar), agenda_config (una fila id=1; UPSERT; recordatorios_horas como array int; persona/
  google_calendar_id opcionales). Reusa exactamente los nombres de columna del modelo.
- faq.py: cargar_faq(seccion_faq, conn) que upserta conocimiento (titulo, contenido, orden, activo=true)
  idempotente por titulo.
- registry.py: un registro declarativo Pack(flag, loader, tablas) con PACKS = {"pack_agenda": Pack(...),
  "pack_faq": Pack(...)}. Helper packs_activos(features_efectivas) -> lista de Pack a correr.

Idempotencia es requisito duro: correr un loader dos veces deja los MISMOS conteos. Logging estructurado
con tenant_id (no print). TDD: tests de integración contra una BD efímera (patrón de
modules/*/tests) que: siembran desde una sección del ejemplo, verifican conteos y relaciones
(recurso->servicios, disponibilidad por día), y recorren el loader comprobando que NO duplica. Corre pytest.
```

**Verificación Fase 2:** tests de idempotencia verdes (doble corrida = mismos conteos); el loader de
agenda reproduce la estructura de `seed_clinica_demo` (3 servicios, 2 recursos, disponibilidad L–V mañana
y tarde, agenda_config con `modo_confirmacion=manual`).

---

## Fase 3 — `provision_from_manifest` (orquestador de un paso)

**Meta:** un comando que hace toda la coreografía, idempotente end-to-end.

```
Contexto: Fase 3 del ADR docs/adr/0007. Existen tools/manifest/ (parser+validación, Fase 1) y
tools/manifest/packs/ (loaders+registry, Fase 2). La base ya la sabe hacer tools/provision_tenant.py
(CREATE DATABASE -> registrar control con URL cifrada -> upgrade head -> admin -> secretos/config/
branding -> plan/empresa_features) y tools/seed_wa_numero.py mapea wa_numeros.

Tarea: crea tools/provision_from_manifest.py con provision_from_manifest(path) -> empresa_id que:
1) carga+valida el manifiesto (Fases 1); si no valida, aborta SIN escribir nada;
2) corre la BASE reusando provision_tenant.provision_tenant_full(datos) — esa función YA hace base +
   control + secretos/config/branding + admin + plan/empresa_features de forma idempotente. Mapea las
   secciones del manifiesto (identidad/admin/plan/features_override/secretos/config/branding) al dict
   `datos` que espera. NO dupliques su SQL; provision_tenant debe seguir funcionando con su JSON actual;
3) calcula las features efectivas y, por cada pack activo (registry.packs_activos), corre su loader sobre
   la conexión del tenant;
4) si hay canal.whatsapp, upserta wa_numeros reusando la lógica de seed_wa_numero (no la dupliques);
5) verifica con un smoke read-only (conteos por tabla) e imprime un resumen de UNA línea
   (ej: "provision_manifest: clinica-demo OK -> 3 servicios, 2 recursos, 4 faq, wa:1176767388843502").
CLI: python -m tools.provision_from_manifest --from <archivo>. Idempotente: re-correr actualiza, no
duplica, y sale 0. Sale !=0 con mensaje claro si algo falla. Driver sync; respeta zona horaria Colombia.

TDD: test e2e contra BD efímera que provisiona desde un manifiesto mínimo (1 servicio, 1 recurso, 1 faq,
1 wa_numero), verifica empresa en control + features + filas de pack + wa_numeros, y RE-CORRE el comando
verificando que los conteos no cambian. Corre pytest.
```

**Verificación Fase 3:** el e2e verde; correr el comando dos veces sobre el mismo manifiesto deja la BD
idéntica; `provision_tenant.py` con su JSON sigue funcionando (no se rompió al refactorizar).

---

## Fase 4 — Validación de aceptación + deprecación del seed bespoke

**Meta:** demostrar que el onboarding ya es declarativo y retirar el código bespoke.

```
Contexto: Fase 4 (aceptación) del ADR docs/adr/0007. Ya existe provision_from_manifest (Fase 3) y el
manifiesto de ejemplo tools/onboarding/clinica-demo.manifest.example.yaml.

Tarea:
1) Escribe un test de aceptación que, sobre una BD efímera, (a) provisione clinica-demo con el seed
   bespoke actual (tools/seed_clinica_demo.py) en una BD, (b) provisione desde el manifiesto en otra, y
   (c) afirme que servicios, recursos, recurso_servicio, disponibilidad y agenda_config quedan
   EQUIVALENTES (mismos conteos y campos clave: nombres, duraciones, precios, días/franjas,
   modo_confirmacion, persona). Si difieren, ajusta el loader/manifiesto hasta que coincidan.
2) Cuando el test pase: marca tools/seed_clinica_demo.py como DEPRECADO (docstring que apunta al
   manifiesto + provision_from_manifest) o elimínalo si nada más lo importa (revisa imports primero).
3) Actualiza docs/onboarding-tenant.md: el flujo nuevo es "escribe el manifiesto + corre
   provision_from_manifest"; deja el flujo viejo (provision_tenant --from JSON) documentado para el lado
   fiscal/POS. Añade una nota en CLAUDE.md (tabla "Dónde está cada cosa") apuntando al ADR 0007.
Corre pytest completo (no solo lo nuevo) para confirmar que nada se rompió.
```

**Verificación Fase 4:** el test de equivalencia verde; `pytest` completo verde; `docs/onboarding-tenant.md`
refleja el flujo declarativo. **A partir de aquí, onboardear = escribir YAML + un comando.**

### Operación en prod (lo hace Andrés, no Claude Code)

Igual que hoy, el provisioning contra producción se corre **EN-RED** vía `railway ssh` (no desde local),
para que la URL del tenant guarde el host privado (gotcha del handoff 8-jun). Lo externo a Kapso/Google
(crear número, webhook, aprobar plantillas, compartir calendario con el service account) sigue siendo
manual por navegador — irreducible.

---

## Fase 5 — *(diferida)* Login real + panel super-admin

No se aborda hasta tener el primer cliente que **edite su propia data**. Hoy: acceso por `dev_token`; un
agente de servicios opera desde WhatsApp + Google Calendar + Inbox de Kapso. Cuando toque, el orden es:
(1) login email/contraseña (hoy NO existe infraestructura de password — es greenfield: columna+hash en
`usuarios`, endpoint, formulario, invitación/reset), reusando el resolver de subdominio que ya existe;
(2) panel super-admin que **es un formulario que produce este manifiesto y llama a este provisionador**.
Por eso este frente va después: el panel se cuelga del provisionador, no al revés.

---

## Checklist de cierre del frente

- [ ] Fase 1: parser+validación verdes.
- [ ] Fase 2: loaders idempotentes (doble corrida = mismos conteos).
- [ ] Fase 3: `provision_from_manifest` e2e idempotente; `provision_tenant` intacto.
- [ ] Fase 4: clinica-demo desde manifiesto ≡ seed bespoke; seed deprecado; docs actualizadas.
- [ ] (Operación) Probado en prod vía `railway ssh` sobre un manifiesto real.
