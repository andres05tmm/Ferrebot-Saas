# Plan — Panel super-admin (onboarding) — Fase B

> Plan de la Fase B del `docs/roadmap-superficies-web.md`. El panel es **"un formulario que produce un
> manifiesto y lo aplica con el provisionador"** (ADR 0007). Depende de A1 (identidades/login) mezclado.
> **Borrador de planeación** (escrito mientras corría A1.3): puede ajustarse cuando A1 aterrice.

## Lo que ya está a favor

- **Provisionador listo:** `provision_from_manifest(<archivo>)` (idempotente: base → packs → wa_numero).
- **Esquema + validación del manifiesto:** `tools/manifest/` (Pydantic + `validar`).
- **Identidades + login:** A1 (control DB), incluye crear identidad admin + enlace de set-password.
- **Worker ARQ/Redis** ya existe (para jobs pesados).
- **RBAC** con rol `super_admin` ya definido (`security.md`).

El panel es **piel sobre todo esto**. Pero hay dos decisiones que no son obvias.

## Decisión B-1 — ¿Cómo PROVISIONA el panel? (la grande)

`provision_from_manifest` es **pesado, privilegiado y debe correr EN-RED**: hace `CREATE DATABASE` +
migraciones (segundos), necesita `ADMIN_DATABASE_URL` (superusuario), y en prod debe correr dentro de la
red de Railway (gotcha del host privado). Eso choca con "llamarlo síncrono desde un request del API".

Tres caminos:

| Opción | Qué es | Veredicto |
|---|---|---|
| **(a) API síncrono** | el endpoint llama `provision_from_manifest` y espera | ❌ request largo + el proceso API necesitaría credenciales de superusuario de Postgres. Riesgoso. |
| **(b) Job async (worker)** | el panel encola un job → el **worker** corre el provisionador → el panel consulta estado | ✅ robusto, reusa el worker que YA existe y corre en-red. Es el destino. |
| **(c) Generador de manifiesto** | el panel arma+valida el YAML; el operador lo corre por `railway ssh` | ✅ v0 sin riesgo: mata el "escribir YAML a mano" sin meter provisioning privilegiado tras un API. |

**Recomendación: progresión (c) → (b).**
- **v0:** el panel es un **formulario que produce un manifiesto VÁLIDO** (reusa `tools/manifest.validar`),
  lo muestra/descarga, y el operador lo aplica (un comando). Valor inmediato, cero infra nueva.
- **v1:** el panel **encola un job** de provisioning en el worker y muestra el estado (en cola → corriendo
  → OK/error con el resumen de una línea). Un clic, de verdad self-serve.

Separar el eje "armar el manifiesto" (fácil, ya) del eje "ejecutarlo" (pesado, privilegiado) es la clave.

## Decisión B-2 — Auth del super-admin (cross-tenant)

El JWT de A1 ata a **un** tenant (claim `tenant`). El super-admin opera sobre la **plataforma** (control
DB), **a través de** tenants — no encaja en un JWT de un solo tenant.

**Recomendación:** una **identidad de plataforma** con `rol=super_admin` cuyo JWT lleva un **scope de
plataforma** (sin atar a un tenant), y las rutas `/admin/*` van **exentas de `TenantMiddleware`**
(como ya está `/auth/login/password`) y **gateadas por `rol=super_admin`**. No reutilizar el claim
`tenant` apuntando a una "empresa plataforma" ficticia (hack).

## Alcance del MVP

**Incluye:** listar tenants (control DB: slug, plan, features, estado, número WA), crear tenant (form →
manifiesto), togglear features/packs por tenant (UI sobre `set_feature`), y gestionar la **identidad admin**
de cada tenant (crear + enviar enlace de set-password, reusando A1).

**Defiere:** embedded signup del número de WhatsApp, billing, analítica. (Van a Fase C / más adelante.)

## Fases propuestas (a afinar tras A1)

| Fase | Entrega | Nota |
|---|---|---|
| **B0** | ADR corto: decide B-1 (ejecución) y B-2 (auth super-admin) | antes de codear |
| **B1** | Auth super-admin (identidad plataforma + JWT de scope plataforma) + API `/admin/*` exenta de middleware, gateada por `super_admin`; endpoint **listar tenants** (lectura control DB) | cimiento |
| **B2** | Frontend: vista de tenants + **formulario de creación** → arma y **valida** el manifiesto (reusa `tools/manifest`). **v0:** descarga/copia el YAML válido | el form es el corazón |
| **B3** | Toggle de features/packs por tenant (UI sobre `set_feature`) + gestión de identidad admin (crear + enlace set-password) | operación diaria |
| **B4** | **v1 de B-1:** encolar job de provisioning en el worker + estado en el panel | self-serve real |

## Decisiones abiertas (para B0, cuando lleguemos)

1. **Ejecución del provisioning:** confirmar progresión (c)→(b). ¿v0 generador basta para arrancar?
2. **Auth super-admin:** ¿identidad de plataforma en una tabla aparte (`admins_plataforma`) o reusar
   `identidades` con un flag/scope de plataforma? (me inclino por algo explícito de plataforma.)
3. **Dónde vive el panel:** ¿una sección del mismo dashboard React gateada por `super_admin`, o una app/
   ruta aparte? (Probablemente misma app, ruta `/admin`, gateada por rol — menos infra.)
4. **Secretos en el form:** si el manifiesto lleva secretos (p. ej. una ferretería con MATIAS), el panel
   NO debe mandarlos en claro ni guardarlos en el navegador; van directo al provisionador/control cifrado.

## Cómo encaja

Con A (login + dashboard limpio) y B (este panel), el flujo de alta deja de necesitar terminal: el
operador llena un form, se valida solo, y (v1) se aprovisiona con un clic. Es exactamente la piel sobre el
provisionador del ADR 0007 — construida **después** del provisionador, no antes.
