# ADR 0010 — Panel super-admin: modelo de ejecución del provisioning + auth de plataforma

> Estado: **Propuesto** (8 jun 2026). Fija las dos decisiones de diseño de la Fase B
> (`docs/plan-panel-superadmin.md`) antes de codear: **cómo provisiona el panel** y **cómo se autentica
> el super-admin** (que opera a través de tenants). Se apoya en el provisionador (ADR 0007), los packs
> (0008) y las identidades/login (0009).

## Contexto

El panel es "un formulario que arma un manifiesto y lo aplica con `provision_from_manifest`". Dos cosas
no son obvias:
1. `provision_from_manifest` es **pesado, privilegiado (CREATE DATABASE) y debe correr EN-RED** — no
   encaja en un request síncrono del API.
2. El JWT del login (ADR 0009) ata a **un** tenant (claim `tenant`), pero el super-admin opera sobre la
   **plataforma** (control DB), **a través de** tenants. El rol `super_admin` ya existe en `rbac.py` y
   hay `require_role`, pero la identidad de A1 ata a una empresa (`empresa_id` NOT NULL).

## Decisión

### D1 — Ejecución del provisioning: job async en el worker (v1)

> **Decidido (8 jun 2026): v1 directo, "bien preparado".** Se descarta el v0 generador como entrega
> intermedia: el panel aprovisiona de verdad (encola un job). A cambio, B2 (el job) carga guardarraíles
> de seguridad explícitos — ver §Guardarraíles de v1 abajo.

Separar **armar el manifiesto** (fácil, ya) de **ejecutarlo** (pesado, privilegiado).

- **v0 — Generador.** El panel arma y **valida** el manifiesto (reusa `tools/manifest.validar`) y lo
  entrega (descarga/copia). El operador lo aplica por `railway ssh` (un comando). Cero infra nueva, cero
  provisioning privilegiado tras un API. Mata el "escribir YAML a mano" desde ya.
- **v1 — Job async.** El panel **encola** un job en el **worker ARQ/Redis** (que ya existe y corre
  en-red); el worker corre `provision_from_manifest`; el panel muestra estado (en cola → corriendo →
  OK/error con el resumen de una línea). Un clic, self-serve real.

*Rechazado:* API síncrono que llama al provisionador (request largo + el proceso API necesitaría
credenciales de superusuario de Postgres). El worker es el lugar correcto para lo pesado/privilegiado.

#### Guardarraíles de v1 (lo que "bien preparado" exige en B2)

- **Validación estricta del `slug`** antes de tocar nada: el slug se vuelve nombre de base
  (`CREATE DATABASE "ferrebot_<slug>"`) → un slug no validado es un vector de inyección de identificador.
  Regex estricto (`^[a-z][a-z0-9-]{1,40}$`) en el esquema del manifiesto **y** revalidado en el job.
- **Solo `super_admin` encola** (`require_role`), y el manifiesto se **valida server-side** (`tools/manifest.validar`)
  antes de encolar — nunca se confía en el cliente.
- **Secretos:** del form → HTTPS → job → cifrados en control DB (patrón del provisionador). Jamás en
  `localStorage`/estado del front, jamás en logs. En la cola (Redis interno) viajan el menor tiempo posible;
  considerar cifrar el payload del job si lleva secretos.
- **Dedup/lock por slug:** dos envíos del mismo slug no deben correr dos `CREATE DATABASE` en carrera
  (lock por slug en Redis o estado de job único por slug). El provisionador es idempotente, pero la carrera
  de creación hay que serializarla.
- **Estado del job** observable (en cola → corriendo → OK/error + resumen de una línea), sin filtrar
  secretos ni rutas internas en el error que ve el panel.

### D2 — Auth del super-admin: identidad de plataforma, JWT sin tenant, rutas `/admin/*` exentas

- El super-admin es un **operador de plataforma** (Andrés). Se autentica con el **mismo login de A1**
  (`/auth/login/password`), reusando el directorio `identidades`.
- **`empresa_id` pasa a NULLABLE** en `identidades`: una identidad `super_admin` es de plataforma, no de
  un tenant. (Las identidades de tenant siguen con `empresa_id` NOT NULL por la lógica de negocio; el
  constraint se relaja solo para permitir el caso plataforma — validar en la capa: `super_admin` ⇒ sin
  empresa; `admin`/`vendedor` ⇒ con empresa.)
- El login, cuando la identidad es `super_admin`, emite un **JWT SIN claim `tenant`** (scope plataforma).
- Las rutas **`/admin/*`** van **exentas del `TenantMiddleware`** (como ya `/auth/login/password`) y
  **gateadas por `require_role("super_admin")`**. Operan sobre el **control DB**, nunca sobre la base de
  un tenant directamente (para tocar datos de un tenant, lo hacen vía el provisionador/herramientas, no
  abriendo su sesión cruzada).

*Rechazado:* atar la identidad super_admin a una "empresa plataforma" ficticia (hack que ensucia el
modelo de tenant). *Rechazado:* un sistema de auth separado para el admin (duplica el login que ya hicimos).

### D3 — El panel vive en el MISMO dashboard React, ruta `/admin`, gateada por rol

Una ruta `/admin` dentro de `dashboard/`, protegida por `ProtectedRoute` + chequeo `rol === super_admin`
(el `/config` o el JWT ya traen el rol). Menos infra que una app aparte. Los tenants normales nunca la ven.

### D4 — Secretos fuera del navegador

En **v0** los secretos del manifiesto (p. ej. MATIAS de una ferretería) los edita el operador en el YAML
local, **nunca en el navegador** → el problema no existe. En **v1**, los secretos viajan por HTTPS al job
y se cifran en el control DB (patrón del provisionador); **jamás** en `localStorage` ni en el estado del
front.

## Alcance del MVP (Fase B)

Incluye: **listar tenants** (control DB), **crear tenant** (form → manifiesto válido; v0 genera, v1
encola), **togglear features/packs** por tenant (UI sobre `set_feature`), **gestionar la identidad admin**
de cada tenant (crear + emitir enlace de set-password, reusando A1). Difiere: embedded signup del número,
billing, analítica.

## Consecuencias

**A favor:** reusa todo lo construido (provisionador, packs, identidades, worker, RBAC); el panel es piel
delgada. v0 entrega valor sin infra nueva ni riesgo de provisioning privilegiado tras un API. La auth de
plataforma cae natural sobre el login de A1 con un cambio mínimo (empresa_id nullable + branch sin-tenant).

**En contra / costo:** relajar `empresa_id` a nullable exige una migración de control + validación de capa
("super_admin sin empresa, el resto con empresa") para no abrir un hueco. Hay que sembrar la **primera
identidad super_admin** (Andrés) con un grandfather/CLI. El branch sin-tenant en el login añade un camino
a testear (que un JWT de plataforma NO resuelva ningún tenant por error).

## Decisiones abiertas (menores)

1. ¿`super_admin` con `empresa_id` NULL, o una tabla `admins_plataforma` separada? (Me inclino por
   nullable + validación: una sola fuente de identidades, menos superficie.)
2. Forma del scope de plataforma en el JWT: ausencia del claim `tenant` vs. un claim explícito
   `scope=platform`. (Me inclino por explícito para que el gate sea inequívoco.)

## Fases (de `docs/plan-panel-superadmin.md`, afinadas)

- **B1** — Auth de plataforma: `empresa_id` nullable + validación; login emite JWT de plataforma para
  `super_admin`; `/admin/*` exenta de middleware + `require_role`; endpoint **listar tenants**. Grandfather
  de la 1ª identidad super_admin (Andrés).
- **B2** — Frontend `/admin`: lista de tenants + **formulario de creación** → arma y valida el manifiesto
  (v0: descarga/copia el YAML).
- **B3** — Toggle de features/packs + gestión de identidad admin del tenant (crear + enlace set-password).
- **B4** — v1: encolar job de provisioning en el worker + estado en el panel.
