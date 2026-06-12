# Alta de una empresa (onboarding)

> **Meta: < 30 min de insumos a dashboard vivo** (plan superficie pública §7). El alta de un tenant de
> servicios (clínica, barbería, restaurante, hotel) es **un manifiesto + un comando**; no se toca el panel
> ni se escribe SQL. El camino fiscal/POS (ferretería, secretos MATIAS) sigue por JSON — ver más abajo.

## Flujo real de punta a punta (tenant-agente de servicios)

1. **Manifiesto desde insumos naturales — skill `onboarding-magico`.** Se le entregan los insumos del
   negocio tal como llegan (fotos de la lista de precios, screenshots de Instagram, Excel/CSV del
   catálogo) y produce un **manifiesto YAML válido** listo para provisionar, bajo el contrato
   **anti-alucinación del ADR 0011** (no inventa precios/servicios; lo que no está en los insumos queda
   marcado para confirmar). Partir de un canónico también vale:
   `cp tools/onboarding/clinica-demo.manifest.example.yaml tools/onboarding/mi-negocio.yaml`.
   El manifiesto declara `identidad` (slug = subdominio), `admin`, `plan.features`, `branding.preset`,
   los `packs` (agenda/FAQ/…) y `canal.whatsapp.phone_number_id`. Con valores reales va **gitignored**.

2. **Provisionar — un comando idempotente.**
   ```bash
   python -m tools.provision_from_manifest --from tools/onboarding/mi-negocio.yaml
   ```
   Ejecuta toda la coreografía: **valida** (falla cerrado, ver abajo) → crea la app DB → la registra en
   el control DB (URL cifrada) → migra (`tenant` head) → siembra catálogo de packs → carga secretos/branding
   → crea el **admin** y las **identidades** → mapea el número de WhatsApp → smoke read-only. Imprime un
   resumen de una línea y los **tokens de set-password** (admin y cada identidad). Re-correr converge al
   mismo estado (idempotente; la garantía es idempotencia, no atomicidad).

3. **Enlace set-password al cliente.** El provisionador NO fija contraseñas: emite un token por identidad
   (`set-password de admin@…: token=…`, guardado en Redis como `sha256(token)`). Se le pasa al cliente el
   enlace `https://app.melquiadez.com/set-password?token=…`; él pone su clave. (Si caduca, pide un reset.)

4. **Branding listo sin diseñar — `branding.preset`.** El preset por vertical (`navaja`, `aurora`, `brasa`,
   `brisa`, `lienzo`, o el default `melquiadez`) hace que el tenant nazca con el look de su gremio:
   `GET /config` entrega los **tokens resueltos** (paleta + radio + fuentes) y el shell los aplica como
   variables CSS. Un `branding.color_primario` explícito GANA sobre el preset (así Punto Rojo conserva su
   rojo). Personalizable después; no bloquea el alta.

5. **Subdominio automático.** Con el wildcard `*.melquiadez.com` apuntando a la API, todo tenant nace con
   `{slug}.melquiadez.com` resolviendo **sin pasos extra**: el resolver lee el subdominio (`core/tenancy/
   resolver.py`). El slug se valida en el manifiesto (`tools/manifest/schema.py`): patrón estricto (se
   materializa en `CREATE DATABASE`) y **no puede ser un label reservado** (`app`, `api`, `www`, `admin`)
   —esos hosts caen al claim del JWT, no a un tenant—.

6. **Mapear el número de Kapso → tenant.** Si el manifiesto no traía un `phone_number_id` real (los demos
   llevan placeholder), se mapea aparte:
   ```bash
   python -m tools.seed_wa_numero <phone_number_id> <slug>     # upsert por phone_number_id
   ```
   Para el número demo compartido se usa `tools/switch_demo` (lo re-apunta y limpia su memoria en Redis).

7. **Verificar — smoke read-only (NO emite facturas).**
   ```bash
   .venv/Scripts/python.exe -m tools.verify_tenant <slug>
   ```
   Comprueba presencia de carga en control/tenant DB, **login real** (con el bot-token descifrado, que
   nunca se imprime) y, con `facturacion_electronica` activa, los catálogos MATIAS producción (solo
   lectura). Verde = dashboard vivo.

> **Punto Rojo (tenant #1)** va por el camino fiscal/POS (JSON, abajo): tras crear DB+migrar se **copian**
> los datos desde el FerreBot legado (ver `architecture.md` §17). FerreBot sigue siendo el nombre del
> producto de Punto Rojo; la plataforma es Melquiadez.

## Dos caminos de provisioning

| Camino | Cuándo | Insumo | Comando |
|---|---|---|---|
| **Manifiesto** (ADR 0007) — *recomendado* | Tenant-agente de **servicios** (clínica, spa, beach club): packs + canal WhatsApp + datos de agenda/FAQ declarativos | un **YAML** por empresa | `python -m tools.provision_from_manifest --from <archivo>` |
| **JSON `provision_tenant`** | Lado **fiscal/POS** (ferretería): secretos MATIAS, Cloudinary, token de bot | un **JSON** por empresa | `python -m tools.provision_tenant --from <archivo>` |

Ambos son **idempotentes** y comparten la misma maquinaria de base (el manifiesto reusa
`provision_tenant_full` para crear la DB, registrar el control, migrar, admin, secretos/branding y
plan/features). El manifiesto **además** carga los datos de pack (agenda/FAQ) y mapea el número de
WhatsApp, lo que el JSON no hace.

## Provisioning declarativo desde manifiesto (ADR 0007) — recomendado

Un **manifiesto por empresa** (un archivo) + un **provisionador de un paso** que ejecuta toda la
coreografía y se puede reintentar sin romper nada (la garantía es **idempotencia**, no atomicidad: tras
un fallo parcial, re-correr el comando entero converge al mismo estado).

1. **Escribir el manifiesto (NUNCA se commitea si lleva valores reales).** Partir del canónico:
   ```bash
   cp tools/onboarding/clinica-demo.manifest.example.yaml tools/onboarding/mi-clinica.yaml
   # editar: identidad (slug/nombre/nit), admin, plan.features + features_override, branding,
   # secretos/config (opcionales; un agente por Kapso suele tener cero), packs.agenda / packs.faq,
   # canal.whatsapp.phone_number_id (lo da Kapso; no es secreto)
   ```
   `tools/onboarding/*.yaml` con valores reales va **gitignored**; solo se versiona el `*.example.yaml`.
   El loader acepta **YAML y JSON** (`yaml.safe_load`).

2. **Validación (falla cerrado).** Antes de tocar la BD, el provisionador valida contra
   `core/tenancy/catalogo.py`: features desconocidas → error; dependencias del set efectivo deben
   cumplirse; **coherencia flag↔datos** (declarar `packs.agenda` exige `pack_agenda` activa; ídem FAQ y
   WhatsApp); franjas `"HH:MM-HH:MM"`, días 0..6, tipo de recurso del enum y `recurso.presta` → servicio
   declarado. Si algo no valida, **no se escribe nada**.

3. **Exportar el entorno** (igual que el flujo JSON: `SECRETS_MASTER_KEY`, `CONTROL_DATABASE_URL`,
   `ADMIN_DATABASE_URL`, `TENANTS_DIRECT_URL_BASE`).

4. **Provisionar:**
   ```bash
   python -m tools.provision_from_manifest --from tools/onboarding/mi-clinica.yaml
   ```
   Imprime un resumen de una línea
   (`provision_manifest: mi-clinica OK -> 3 servicios, 2 recursos, 20 disponibilidad, 4 faq, wa:1176…`).
   Re-ejecutar es seguro (todo UPSERT por clave natural).

## Provisioning automatizado (operador) — lado fiscal/POS (JSON)

`tools/provision_tenant.py` cubre los pasos 2-6 de forma **idempotente** desde un JSON de onboarding:
crea la app DB, la registra en el control DB (URL cifrada), migra, siembra el admin, y carga
**secretos cifrados** (`secretos_empresa`), **config fiscal en claro** (`config_empresa`), **branding**
y el **`telegram_id`** real del admin.

1. **Rellenar el insumo (NUNCA se commitea).** Copiar el ejemplo y poner valores REALES:
   ```bash
   cp tools/onboarding/empresa.example.json tools/onboarding/puntorojo.json
   # editar puntorojo.json: slug, nombre, nit, admin.telegram_id, secretos (telegram_token,
   # matias_email, matias_password), config (matias_*), branding (color/logo/nombre/dominio),
   # plan + features_override (capacidades)
   ```
   `tools/onboarding/*.json` está en `.gitignore` (solo `empresa.example.json` se versiona). Los
   secretos van **cifrados** en el control DB con `SECRETS_MASTER_KEY`; nunca quedan en claro en git.

   **Capacidades (`plan` / `features_override`):**
   ```json
   "plan": { "nombre": "Pro", "features": ["facturacion_electronica", "fiados"] },
   "features_override": { "ventas_voz": false }
   ```
   - `plan.features` = capacidades del tier; `features_override` = excepciones por empresa
     (`true` activa, `false` desactiva). Efectivas = plan ∪ overrides activos − desactivados;
     el **núcleo** (ventas, inventario, caja, gastos, clientes, proveedores, reportes) está **siempre on**.
   - El provisioning **valida** contra `core/tenancy/catalogo.py`: nombres desconocidos → error; y las
     **dependencias** del set efectivo deben cumplirse (p. ej. `libro_iva` requiere `facturacion_electronica`
     o `compras_fiscal`). Si algo no valida, **no se escribe nada**.
   - **⚠️ Caveat — tier compartido:** el plan se upserta por **NOMBRE**. Cambiar `plan.features` de un
     nombre ya existente afecta a **todas** las empresas de ese plan. Para variar capacidades de una sola
     empresa, usa `features_override` (o un nombre de plan distinto). La consistencia es del operador.
   - Sin bloque `plan` ni `features_override`, la empresa arranca **solo con el núcleo**.

2. **Exportar el entorno** (la misma `SECRETS_MASTER_KEY` que usará la API/bot, o no se podrán descifrar):
   ```bash
   export SECRETS_MASTER_KEY=...        # IMPRESCINDIBLE que coincida con el runtime
   export CONTROL_DATABASE_URL=...      # control DB
   export ADMIN_DATABASE_URL=...        # superusuario para CREATE DATABASE
   export TENANTS_DIRECT_URL_BASE=...   # base de las app DB por empresa
   ```

3. **Provisionar:**
   ```bash
   .venv/Scripts/python.exe -m tools.provision_tenant --from tools/onboarding/puntorojo.json
   ```
   Re-ejecutar es seguro (UPSERT por `(empresa_id, clave)` / `empresa_id`): no duplica.

4. **Verificación (read-only; NO emite facturas) — `tools.verify_tenant`:**
   ```bash
   .venv/Scripts/python.exe -m tools.verify_tenant <slug>     # default: puntorojo
   ```
   Automatiza el smoke: **login real** (con el bot-token descifrado, que nunca se imprime → `POST
   /api/v1/auth/login` **200**) y, con `facturacion_electronica` activa, los **catálogos MATIAS
   producción** (`/clientes/ciudades`, `/clientes/paises`, solo lectura). **La emisión real de facturas
   queda FUERA de este flujo, deliberada.**

5. **Bot:** registrar el webhook `/tg/{slug}` con el token cargado (`secretos_empresa.telegram_token`).

## Tenants demo (superficie pública Melquiadez) — 4 verticales

Cada demo es un **tenant real provisionado por manifiesto** (mismo camino que un cliente pagado, así
cada demo es un test de provisioning). Los manifiestos se versionan (no llevan secretos):

| Slug | Negocio | Packs | Manifiesto |
|---|---|---|---|
| `clinica-demo` | Clínica Aurora | `pack_agenda`, `pack_faq`, `canal_whatsapp` | `tools/onboarding/clinica-demo.manifest.example.yaml` |
| `barberia-demo` | El Patio | `pack_agenda`, `pack_faq`, `canal_whatsapp` | `tools/onboarding/barberia-demo.manifest.example.yaml` |
| `restaurante-demo` | Brasa | `pos`, `pack_pedidos`, `pack_faq`, `canal_whatsapp` | `tools/onboarding/restaurante-demo.manifest.example.yaml` |
| `hotel-demo` | Brisa | `pack_agenda`, `pack_reservas`, `pack_faq`, `canal_whatsapp` | `tools/onboarding/hotel-demo.manifest.example.yaml` |

### Alta de un tenant DEMO (qué lo hace distinto de un cliente)

Un demo se provisiona **exactamente igual** que un cliente pagado (mismo `provision_from_manifest`), con
tres particularidades:

1. **Identidad demo no-admin.** Además del admin, el manifiesto declara una identidad
   `demo+<slug>@melquiadez.com` con rol `vendedor` (no admin: que un prospecto pruebe el dashboard sin
   poder romper la demo). Su contraseña se fija por el enlace de set-password que imprime el provisionador
   —o, para una demo pública, se le pone una clave corta conocida con un reset—. El botón "Ver demo" de la
   landing hace el login de esta identidad y cae en `{slug}.melquiadez.com`.

2. **Marca de demo = lista de slugs en config, NO una columna `es_demo`.** El plan §4 contemplaba una
   columna `es_demo` en el control DB; la implementación la **descartó** a favor del setting
   `demo_tenant_slugs` (`core/config/settings.py`, default
   `clinica-demo,barberia-demo,restaurante-demo,hotel-demo`): cero migración, reversible, y el único
   consumidor hoy es el cron de resiembra. Marcar/desmarcar un demo = editar esa lista (por entorno en
   prod). Si algún día el panel super-admin necesita filtrar demos en SQL, se promueve a columna.

3. **Datos vivos con higiene nocturna** (siguiente sección): a diferencia de un cliente, sus datos
   transaccionales se **resiembran** cada noche para que la demo siempre amanezca impecable.

Verificar un demo recién provisionado es el mismo `python -m tools.verify_tenant <slug>` del flujo real.

> **Nombre de plan único por demo:** los planes se comparten por NOMBRE (ver caveat arriba), así que cada
> demo tiene su propio plan (`Demo Barbería`/`Restaurante`/`Hotel`) para que sus capacidades efectivas
> sean las suyas. No reutilizar un nombre de plan entre verticales distintos.

### Datos vivos + higiene nocturna

El provisionador siembra **config/catálogo** (servicios, recursos, menú POS, FAQ). Los **datos
transaccionales vivos** (citas/reservas/pedidos con fechas relativas a hoy) los pone
`tools/seed_demo_transaccional.py`, que además es la operación de **reset**: borra lo transaccional y
resiembra relativo a `now_co` (idempotente). El cron `resembrar_demos` (worker ARQ, ~04:10 Colombia) lo
corre cada noche para todos los `settings.demo_tenant_slugs` → las demos siempre amanecen llenas.

Siembra/reset manual:
```bash
python -m tools.seed_demo_transaccional               # todos los demo_tenant_slugs
python -m tools.seed_demo_transaccional --slug barberia-demo   # uno
```

### Provisionar las demos en producción (EN-RED, `railway ssh` al Worker)

Igual que el resto de provisioning/migraciones: **en-red** (para que la URL del tenant guarde el host
privado) y en el **servicio Worker** (tiene `ADMIN_DATABASE_URL` + `SECRETS_MASTER_KEY`). Patrón del
runbook (`docs/DEPLOY-RAILWAY-PILOT.md §6`):

```bash
railway ssh                       # contenedor del Worker (no el API)

# 1) Provisionar las 3 demos nuevas (clinica-demo ya existe; re-correrla es idempotente).
python -m tools.provision_from_manifest --from tools/onboarding/barberia-demo.manifest.example.yaml
python -m tools.provision_from_manifest --from tools/onboarding/restaurante-demo.manifest.example.yaml
python -m tools.provision_from_manifest --from tools/onboarding/hotel-demo.manifest.example.yaml
# Cada uno imprime su resumen + los tokens de set-password (admin y demo). Guárdalos o usa /reset.

# 2) Sembrar los datos vivos por primera vez (luego el cron nocturno los mantiene frescos).
python -m tools.seed_demo_transaccional

# 3) (Opcional) Apuntar el número Kapso de demo al vertical que vas a mostrar — ver §6 del plan
#    Melquiadez (tools/switch_demo, frente aparte).
```

> Los `phone_number_id` de los manifiestos demo son placeholders (`demo-<vertical>-0001`): el número
> real de Kapso se re-apunta al vuelo con el switch de demos. `DEMO_TENANT_SLUGS` puede sobreescribirse
> por entorno si en prod conviven menos/más demos.
