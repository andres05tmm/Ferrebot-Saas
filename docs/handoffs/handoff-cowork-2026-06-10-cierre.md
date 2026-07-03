# Handoff Cowork — Cierre 10 jun 2026 (switch-on POS: prep completo + deuda saldada)

> Léelo al iniciar la sesión nueva, junto con `docs/handoff-cowork-2026-06-09-fable5-innovacion.md`
> (rol, patrón de trabajo y principio económico de Fable siguen vigentes tal cual ahí) y
> `docs/handoff-cowork-2026-06-09-cierre.md` (contexto de la sesión previa).
> **Rol Cowork:** senior que escribe ADRs/planes y prompts para Claude Code, REVISA cada diff
> **leyendo el código real** (no el resumen), y **no ejecuta git** — lo hace Andrés en Windows.
> **Regla no negociable:** toda sesión paralela de Claude Code trabaja en un **git worktree propio**.

## Estado al cierre

- `main` = `0846395` (PRs #6–#11 mezclados, CI verde). Local sincronizado, worktrees limpios.
- Railway prod redesplegado con todo lo de hoy. Flag `pos_electronico` **APAGADO** (POS inerte).
- Resolución DIAN POS de Punto Rojo: emitida, **se activa el 11-jun** (la DIAN es lenta). Por eso
  el switch-on real queda para mañana.

## Qué se hizo hoy (todo: revisión del diff real → CI verde → merge)

1. **PR #6 mezclado** (POS electrónico F2.1+F2.2, flag apagado). **Migraciones desplegadas en prod**
   (EN-RED vía `railway ssh` al **Worker**): control `0007_webhooks_matias` + tenant
   `0013_fe_xml_historico` / `0014_webhooks_recibidos` / `0015_fe_tipo_pos` → `puntorojo` y `clinica-demo`.
2. **Config POS pre-cargada** en `puntorojo` (`config_empresa`, flag apagado):
   `matias_resolution_pos=18764110996067`, `matias_prefix_pos=POS`, `pos_terminal=CJ01`,
   `pos_address="BRR ALCIBIA CL 31 CR 30 72 P 1 LT 1"`, `pos_cashier_type=GENÉRICA`.
   (`cashier_type` es **string libre** en MATIAS — ej. "GENÉRICA"/"Caja de apoyo" —, no un enum.)
3. **Webhook MATIAS registrado** para `puntorojo` (`/webhooks/matias/<token>`, secret cifrado).
   Aplica ya a la FE manual (concilia/archiva). Dos bugs encontrados y arreglados (PR #7): el
   endpoint exige campo **`name`** (la doc lo omite → 422) y la URL es **`{base}/api/ubl2.1/webhooks`**
   (con `/api`).
4. **Tanda de deuda — 5 PRs, todos revisados y mezclados:**
   - **#7** `fix/webhook-matias-name`: `name` obligatorio en `registrar_webhook` + 422 con body
     legible + `_registro_url` normaliza `/api`.
   - **#8** `feat/fe-estado-anulada`: estado fiscal **`anulada`** (`document.voided`), migración
     tenant **`0016_fe_estado_anulada`** (`ADD VALUE`, autocommit) — **ya aplicada en prod**.
     + `docs/facturacion-dian.md` alineado: emisión **síncrona** `pendiente → aceptada|rechazada|error`;
     **`enviada` marcado RESERVADO** (no usado; implementarlo requeriría su propio ADR).
   - **#9** `chore/provisioner-resumen-pos`: el `_resumen` cuenta `facturas_electronicas` +
     `webhooks_matias_recibidos`.
   - **#10** `fix/tenant-nombre-v2`: `ResolvedTenant.nombre` (el default de `--name` del webhook usa
     el nombre real del tenant).
   - **#11** `fix/auth-reset-ratelimit`: rate-limit de `/auth/reset/solicitar` en **dos cubos
     INDEPENDIENTES** (por email solo, inmune a rotación de IP → frena email-bombing; por IP sola,
     best-effort) + dejar de loguear el token (solo `token_ref`).

## Pendientes (en orden) — MAÑANA, con la resolución DIAN ya activa

1. **Gate en sandbox MATIAS** (no quema consecutivo, no necesita la resolución activa). El sandbox
   existe (`sandbox-api.matias-api.com`), paridad total, **las credenciales de prod se replican solas**;
   fuerza estados con `X-Sandbox-Force-Status`. Lanzar el smoke (prompt abajo): confirma el shape de
   la respuesta del tipo 20 / autoincremento contra `_parsear_emision_pos`. **Decisión de `enviada`:**
   solo se implementa si el smoke muestra que MATIAS responde "en proceso" **sin** estado final; si
   trae CUFE síncrono, queda reservado como está (Eje 1 = validación DIAN; nada que ver con RADIAN,
   que es el Eje 2 de compras).
2. **Registrar la resolución POS** (`18764110996067`, prefijo `POS`) en el portal MATIAS.
3. **Prender el flag:** `python -m tools.set_feature puntorojo pos_electronico on` (o panel `/admin`),
   en el Worker vía `railway ssh`. **Venta de prueba** de mostrador (bot **y** dashboard) → verificar
   CUDE en el dashboard + webhook recibido.
4. **F2.3** (superficie dashboard: estado fiscal POS/FE/pendiente/error/**anulada**, flujo "cliente
   pide factura" con exclusión POS↔FE, alerta de antigüedad) y **F2.4** (sección fiscal POS en el
   manifiesto — cruza con el pack POS del ADR 0011).
5. **Frente B — Bre-B:** research de PSPs (Wompi/Bold, estado jun-2026) por Cowork → ADR 0013.

### Prompt del gate (sandbox) para Claude Code — Opus 4.8, worktree propio

```
Trabaja en git worktree propio (rama chore/smoke-pos-sandbox), NO en el working dir compartido.
Un commit, sin merge. Convenciones del repo; sin secretos en código.

Objetivo: smoke de un solo uso que CONFIRMA contra el sandbox de MATIAS el shape de la respuesta
del documento equivalente POS (tipo 20) por autoincremento, validando el parser real
_parsear_emision_pos (la doc de matias_client.py marca este punto como "se confirma contra sandbox").

Crea tools/smoke_pos_sandbox.py que:
1. REUSA el código real (lee las firmas exactas): modules/facturacion/ubl.py::armar_payload_pos,
   los schemas de modules/facturacion/schemas.py (PosInput y partes), y
   modules/facturacion/matias_client.py (MatiasClient + MatiasCredenciales + emitir_pos +
   _parsear_emision_pos).
2. Credenciales por ARGS/ENV (jamás control DB ni secretos en código): --email/--password
   (o MATIAS_SMOKE_EMAIL/PASSWORD) = credenciales MATIAS de Punto Rojo; --base-url
   (default https://sandbox-api.matias-api.com/api/ubl2.1); --resolution (default 18764110996067).
3. Construye un PosInput representativo (1-2 ítems IVA 19% incluido, CONSUMIDOR FINAL, point_of_sale
   con cashier_name="VENDEDOR PRUEBA", terminal_number="CJ01", address, cashier_type="GENÉRICA",
   sales_code="POS-SMOKE-1", sub_total calculado). Arma el payload con armar_payload_pos.
4. Instancia MatiasClient con base_url=sandbox, llama await emitir_pos(payload) e IMPRIME: payload
   enviado, respuesta CRUDA (resultado.raw), y parseado (ok, categoria, cufe, numero, prefijo,
   error_msg). Además una llamada httpx directa para volcar los HEADERS (confirmar
   X-MATIAS-Environment: sandbox) y el body crudo. Cierra el cliente (aclose).
5. Error robusto: si MATIAS responde error, NO crashees — imprime status HTTP + body.

No toques el flag ni tools/manifest. No hagas merge. Reporte: archivos, comando de ejemplo, salida.
```

## Deuda anotada

- `print` del token de reset en `tools/provision_tenant.py:292` (CLI interactivo al operador) sigue —
  bajo riesgo, diferido.
- `_registro_url` del webhook asume la instancia estándar de MATIAS (path `/api/ubl2.1`).
- `enviada` reservado en el enum `fe_estado` (quitarlo en PG es costoso; decisión de implementarlo
  depende del gate de mañana → ADR si aplica).
- Vigentes del handoff anterior: plantilla `recordatorio_cita` de Kapso; onboarding v2 (worker+router)
  cuando haya volumen; **el bottleneck sigue siendo el primer cliente real** — el paquete de venta ya
  existe (onboarding mágico + "te pongo legal con la DIAN").

## Gotchas operativos (nuevos de hoy)

- **El git del sandbox de Cowork NO es confiable para este repo.** El mount mostró el working tree
  "corrupto" (archivos `.py` truncados, nombres basura `l\020`/`r`, falsos `UU`, ~1487 deleciones),
  pero el repo real de Windows estaba **sano** (`git status` limpio, `fsck` solo dangling). → El git
  se opera **solo desde Windows/Claude Code**; para leer código a revisar usar **GitHub raw**
  (`/raw/<rama>/<archivo>`), nunca el sandbox.
- **`railway ssh` para migraciones/tools = servicio Worker** (tiene `ADMIN_DATABASE_URL` +
  `SECRETS_MASTER_KEY` + red privada). Contenedor **FRESCO** tras cada redeploy (el viejo corre código
  viejo). `railway link` ≠ `railway ssh`: link solo enlaza; ssh abre el shell `:/app#`.
- **Worktrees y base desactualizada:** si Claude Code corta ramas de un `main` local viejo, una rama
  que toca archivos ya cambiados por otro PR mezclado **choca/regresa** (pasó: la rama del nombre se
  basó en pre-#7 y reimplementó `--name` como opcional, regresando #7). → Antes de lanzar worktrees,
  **sincronizar `main` local** (`fetch`+`pull`) y verificar en el prompt que la base incluye los PRs
  relevantes.
- **MATIAS:** webhook se registra en `{base}/api/ubl2.1/webhooks` (con `/api`) y EXIGE `name` (no
  documentado). `cashier_type` es string libre. Sandbox en `sandbox-api.matias-api.com`.
- **Pegar multilínea en `railway ssh` se mangle** (el heredoc se pega pegado). Usar one-liner base64:
  `echo '<b64>' | base64 -d | python -`.
- `PostToolUse:Bash hook error` (node cjs/loader) en Claude Code es non-blocking → ignorar.
- Borrar rama de un worktree: primero `git worktree remove <ruta real> --force`, luego `git branch -d`.

## Asignación de modelos (vigente)

Fable 5 para razonamiento abierto / audits / diseño de prompts; **Opus 4.8** para implementación
pautada por ADR/prompt; modelos livianos para extracciones del skill. Cowork (Fable) hace research web
+ revisión de diffs sin costo de API. Andrés opera Railway/MATIAS/GitHub por navegador (Cowork tiene
Claude in Chrome con sus sesiones).
