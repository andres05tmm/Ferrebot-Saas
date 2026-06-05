# Roadmap — FerreBot SaaS (Fases 7+)

> Plan de avances grandes desde el cierre de Fase 6. **Objetivo doble** (decidido con Andrés):
> **Punto Rojo operando** en el SaaS **y** producto **SaaS completo con dashboard**.
> Este documento manda sobre `.planning/roadmap.md` (que queda como visión estratégica de alto nivel).
> Al retomar entre sesiones, leer `docs/handoff.md` → este archivo.

## Dónde estamos

- **Fases 1-6 cerradas** (implementación): núcleo multi-tenant, dominio (ventas/caja/fiados/clientes/
  inventario/memoria), auth/RBAC, eventos SSE, bot Telegram, **emisión DIAN síncrona** completa y probada.
- **318 tests** verdes (con Postgres + Redis arriba). Ramas `feat/fase-5-bot` y `feat/fase-6-facturacion`
  **sin mergear** a `main`.
- Estimado: **~45-55%** del proyecto. Lo hecho es lo más duro (cimientos backend + bot + emisión). Falta
  **amplitud**: dashboard, resto de facturación, operación real, capa SaaS.

## Lectura del objetivo doble

"Punto Rojo operando" y "SaaS completo" **comparten casi todo el camino crítico**: el dashboard, las
feature flags y el provisioning son tanto requisito para sacar a Punto Rojo de FerreBot original como la
base del producto vendible. La diferencia real es el **último tramo**: Punto Rojo necesita migración de
datos + deploy; el SaaS necesita billing + multi-empresa pulido. Por eso el roadmap es **un solo tronco**
con dos hitos al final:

- **M1 — Punto Rojo operando:** la ferretería real corriendo sobre el SaaS (datos migrados, en Railway).
- **M2 — SaaS completo:** alta de empresas autoservicio, billing y segunda empresa-cliente.

## Mapa de fases

| Fase | Bloque | Sirve a | Camino crítico |
|---|---|---|---|
| 7 | Consolidación + guardarraíles | ambos | sí (desbloquea `main`) |
| 8 | Cierre de esquema del tenant (brechas §8) | ambos | **sí** (prereq de 11, 12, 15) |
| 9 | Feature flags efectivas + `GET /config` | ambos | **sí** (prereq de dashboard) |
| 10 | Resolución asíncrona DIAN | M1 | paralelizable |
| 11 | Dashboard web React (MVP white-label) | ambos | **sí** |
| 12 | Amplitud de facturación | M1 | sí (Punto Rojo usa todo lo fiscal) |
| 13 | Provisioning automatizado + onboarding | ambos | **sí** |
| 14 | Deploy producción + observabilidad + DR | M1 | sí |
| 15 | Migración Punto Rojo (ETL + paridad) → **M1** | M1 | sí |
| 16 | SaaS comercial (billing/planes/2ª empresa) → **M2** | M2 | sí |
| 17 | Escala (futuro) | — | no |

**Paralelizable:** la Fase 10 (asíncrono DIAN) no bloquea el dashboard; pueden ir en paralelo si hay manos.
La Fase 11 (dashboard) y la 12 (amplitud facturación) comparten el patrón router+tab y conviene hacer el
backend de un documento fiscal y su tab juntos, pero el **núcleo** del dashboard (Fase 11) debe cerrarse
antes de abrir los tabs fiscales.

---

## Fase 7 — Consolidación y guardarraíles

**Objetivo:** dejar `main` al día y cerrar la deuda de Fase 6 que oculta bugs, antes de construir encima.

**Entregables**

- **E1 — Merge ordenado a `main`:** integrar `feat/fase-5-bot` y `feat/fase-6-facturacion` (en ese orden),
  resolver conflictos, suite verde en `main`. *(Andrés ejecuta el merge; Cowork revisa el diff.)*
- **E2 — Smokes HTTP de routers existentes:** ventas/caja/inventario/fiados golpeados por HTTP con
  `dependency_overrides` + `ASGITransport` (patrón de `test_e2e_facturacion`). Cierra la deuda que ocultó
  el bug de `get_tenant_db` sin `request: Request`.
- **E3 — Salud:** `GET /health` y `GET /ready` (DB + Redis), para Railway y monitoreo.
- **E4 — Caché de `MatiasClient` por tenant** en el worker (reusar token/ciudades; hoy se arma uno por
  emisión). Optimización con test.

**Dependencias:** ninguna. **Criterio de cierre:** `main` con suite verde + smokes HTTP de los 4 routers
núcleo + `/health`/`/ready` respondiendo. **Riesgo:** bajo. Conflictos de merge entre fase-5 y fase-6.

---

## Fase 8 — Cierre de esquema del tenant (brechas §8)

**Objetivo:** ampliar el esquema de la app DB para que soporte **paridad real** con FerreBot. Es prerequisito
de la amplitud de facturación (12), de los tabs del dashboard (11) y del ETL (15). Resolver las decisiones de
`migracion-puntorojo.md` §8 **antes** de codificar.

**Entregables** (cada uno = decisión + migración `migrations/tenant/` + modelos + repos + tests)

- **E1 — Catálogo extendido:** tabla `productos_fracciones` (722 filas en origen) y soporte de `aliases`
  (tabla + uso en bypass/IA). Decidir **pricing escalonado** (`precio_umbral/bajo/sobre`) vs solo
  `precio_mayorista`. → ADR.
- **E2 — Cuentas por pagar / proveedores:** `proveedores`, `facturas_proveedores`, `facturas_abonos`.
- **E3 — Documentos fiscales aparte:** `documentos_soporte` (CUDE), `cuentas_cobro` con `cliente_id`
  **nullable** (honorarios sin cliente).
- **E4 — Conciliación y reportes:** `bancolombia_transferencias`, `historico_ventas` (decidir tabla de
  rollup vs reconstruir desde `ventas`).
- **E5 — Modelo de caja y gasto↔caja:** confirmar apertura/arqueo vs agregado diario; cómo todo gasto
  mueve caja (reconstruir el vínculo `caja_id` que FerreBot no tiene).

**Dependencias:** Fase 7. **Criterio de cierre:** `alembic upgrade/downgrade` limpio en tenant; `schema.md`
actualizado; cada brecha §8 con decisión registrada (ADR cuando aplique). **Riesgo:** medio — decisiones de
modelado que afectan ETL y facturación. Tratar como **checkpoints** con recomendación antes del RED.

---

## Fase 9 — Feature flags efectivas + `GET /api/v1/config`

**Objetivo:** capacidades por empresa de punta a punta. Hoy solo existe `require_feature("facturacion_
electronica")`. Es el cimiento que el dashboard necesita para ocultar tabs y el SaaS para diferenciar planes.

**Entregables**

- **E1 — Catálogo + almacenamiento (control DB):** `planes.limites.features`, tabla `empresa_features`
  (overrides), cálculo de **capacidades efectivas** = plan ± overrides, cacheado en el contexto del tenant.
- **E2 — Enforcement backend:** `require_feature(...)` en **todos** los routers fiscales/opcionales (404
  genérico si off), no solo facturación. Validación de dependencias (no activar `notas_electronicas` sin
  `facturacion_electronica`).
- **E3 — `GET /api/v1/config`:** endpoint de arranque del dashboard → branding + `features` efectivas + datos
  de empresa. Fuente única para el front.
- **E4 — Admin de flags:** `PUT /api/v1/admin/empresas/{id}/features` (super_admin) con invalidación de caché.

**Dependencias:** Fase 7. **Criterio de cierre:** un test que active/desactive una flag y verifique 404 en el
router + ausencia en `/config`; dependencias validadas. **Riesgo:** bajo-medio.

---

## Fase 10 — Resolución asíncrona del estado DIAN

**Objetivo:** cerrar la deuda explícita de Fase 6: hoy el pipeline es síncrono (`pendiente → aceptada|error`
según la respuesta inmediata de `/invoice`). La aceptación REAL de la DIAN puede tardar.

> **Bloqueante de inicio:** confirmar el contrato de `/status/document` y `/documents` **contra el sandbox
> MATIAS** (`MATIAS_AMBIENTE=pruebas`) y el **formato de montos** (number vs string, decimales, redondeo).
> No construir sobre suposiciones.

**Entregables**

- **E1 — E4c reconciliador:** job ARQ periódico que consulta `/status` por CUFE y reconcilia
  `enviada → aceptada|rechazada`, con la política de reintento/dead-letter ya existente.
- **E2 — E4d webhook:** endpoint para el callback de MATIAS (si lo ofrece), idempotente, que actualiza estado
  + emite `pg_notify`.
- **E3 — Formato de montos confirmado** y, si difiere, ajuste en `_a_json`.

**Dependencias:** Fase 7; **sandbox MATIAS disponible**. **Paraleliza con 11.** **Criterio de cierre:** smoke
que simule aceptación diferida y verifique la reconciliación. **Riesgo:** medio — depende de un contrato
externo no fijado.

---

## Fase 11 — Dashboard web React (MVP white-label)

**Objetivo:** el mayor pendiente individual. **No existe nada** en el repo. Dashboard PWA white-label que
sirve tanto a Punto Rojo (pantalla operativa) como al SaaS.

**Entregables**

- **E1 — Andamiaje:** React + Vite en `dashboard/`, servido como estáticos por FastAPI; tema por defecto
  `#C8200E`, branding desde `GET /config`.
- **E2 — Auth:** Telegram Login Widget + JWT; guard de rutas; logout; rol en el token.
- **E3 — Layout + gating por features:** tabs ocultos/visibles según `features` de `/config`. Selector de
  vendedor solo para admin (RBAC).
- **E4 — Tabs núcleo:** ventas, inventario, caja, clientes, reportes (los del catálogo "núcleo siempre
  activo").
- **E5 — Tiempo real:** consumer SSE (`useRealtime` con backoff + evento `reconnected` → re-fetch).
- **E6 — PWA + cola offline:** service worker, cola de operaciones (venta/emisión) con **idempotencia**
  (`Idempotency-Key`) al reconectar. Ver `offline-sync.md`.

**Dependencias:** Fase 9 (`/config` + features), Fase 8 (tablas que los tabs leen). **Criterio de cierre:**
login real, los 5 tabs núcleo operativos contra el backend, SSE actualizando en vivo, instalable como PWA con
una operación encolada offline que se sincroniza. **Riesgo:** alto por tamaño — fasear por tab; cerrar el
andamiaje (E1-E3) antes de abrir tabs.

---

## Fase 12 — Amplitud de facturación

**Objetivo:** portar de FerreBot original el resto de lo fiscal. Punto Rojo **usa todo** esto (ver el ejemplo
de `feature-flags.md`), así que es requisito de M1, no opcional.

**Entregables** (cada uno: backend router+service+repo bajo su `require_feature`, + tab en dashboard)

- **E1 — Documento soporte (DS-NO):** compras a no obligados, resolución propia, consecutivo DS con CUDE.
- **E2 — Notas crédito/débito:** referencia a la factura original; flag `notas_electronicas`.
- **E3 — Eventos RADIAN** (acuse, aceptación) sobre compras_fiscal.
- **E4 — Libro IVA:** tab + saldos bimestrales (`iva_saldos_bimestrales`).
- **E5 — Compras / compras fiscal:** tab Compras fiscal con soporte tributario.
- **E6 — Proveedores + Cloudinary:** facturas de proveedores con foto (subida cifrada por empresa).
- **E7 — Honorarios:** cuentas de cobro (cliente nullable).

**Dependencias:** Fases 8 (esquema), 9 (flags), 11 (patrón de tab), 10 recomendable (estado real). **Criterio
de cierre:** cada documento emite/registra y aparece en su tab gated por flag; tests por documento. **Riesgo:**
medio — volumen; portar verbatim como en Fase 6.

---

## Fase 13 — Provisioning automatizado + onboarding

**Objetivo:** dar de alta una empresa de forma automatizada (es el primer paso del alta de Punto Rojo y la
base del autoservicio SaaS).

**Entregables**

- **E1 — `tools/provision_tenant`:** crear base → `alembic upgrade head` (tenant) → semilla → secretos
  cifrados (MATIAS/Cloudinary/bot) → branding → admin. Idempotente. Ver `onboarding-tenant.md`.
- **E2 — Registro de bot:** registrar webhook `/tg/{empresa}` con el token de su bot.
- **E3 — Panel super-admin (onboarding):** crear empresa (nombre, NIT, slug/subdominio, plan), cargar
  secretos, marcar `estado=activa`. Sobre el dashboard de la Fase 11.
- **E4 — Smoke de provisioning E2E:** provisionar empresa de prueba → venta + emisión de prueba.

**Dependencias:** Fases 8, 9, 11. **Criterio de cierre:** una empresa nueva provisionada por script/panel,
con smoke verde. **Riesgo:** medio — manejo de secretos cifrados (`SECRETS_MASTER_KEY`).

---

## Fase 14 — Deploy producción + observabilidad + DR

**Objetivo:** infraestructura lista para correr Punto Rojo de verdad.

**Entregables**

- **E1 — Railway:** servicios API + bot + worker (ARQ), PgBouncer, Redis; variables por servicio
  (ver `infra-railway.md`). `SERVICE_TYPE` por proceso.
- **E2 — Observabilidad:** Sentry + logging estructurado (`tenant_id`/`request_id`) visible en Railway.
- **E3 — Backups y DR probados:** backup de control DB + cada app DB; **restore probado** (no solo
  configurado). Histórico fiscal DIAN ~5 años.
- **E4 — Email transaccional** (altas, recuperación, avisos).

**Dependencias:** Fase 13. **Criterio de cierre:** entorno de producción levantado, `/health` verde,
restore de una DB demostrado. **Riesgo:** medio.

---

## Fase 15 — Migración Punto Rojo (ETL + paridad) → **M1**

**Objetivo:** el hito. Pasar los datos reales de FerreBot al tenant #1 y cortar.

**Entregables** (siguen `migracion-puntorojo.md` §5-§9)

- **E1 — Script ETL idempotente:** carga por lotes en el orden de FKs (§5), upsert por PK preservada, con las
  transformaciones globales G1-G8 (dinero `NUMERIC`, fechas naive→UTC Colombia, etc.).
- **E2 — Secuencias y consecutivos** (§6): `setval` de PKs + **continuidad DIAN** (consecutivo legal de
  factura/DS = `max` real, no el id_seq).
- **E3 — Validación de paridad** (§9): conteos, sumas de control (ventas/facturas/fiados/IVA), continuidad de
  CUFEs/CUDEs, FKs sin huérfanos, muestra de fechas G4, smoke (venta + emisión + cierre), test de aislamiento
  (A nunca ve B) con Punto Rojo como tenant real.
- **E4 — Corte:** conectar bot + dashboard al tenant, corte de webhooks de FerreBot original.

**Dependencias:** Fases 8 (esquema), 12 (fiscal), 13 (provisioning), 14 (prod). **Criterio de cierre:**
**Punto Rojo operando** sobre el SaaS en Railway, paridad validada, FerreBot original apagado o en solo-lectura.
**Riesgo:** alto — G4 (zona horaria de las marcas naive) es la transformación de mayor riesgo; confirmar
**antes** de cargar.

---

## Fase 16 — SaaS comercial (billing + planes) → **M2**

**Objetivo:** convertir la plataforma en producto vendible.

**Entregables**

- **E1 — Planes y cuotas:** medición de uso por plan, límites (empresas, vendedores, emisiones).
- **E2 — Billing enchufable:** integración de cobro (o cobro manual documentado) + estado de suscripción.
- **E3 — Onboarding autoservicio pulido:** alta de empresa de extremo a extremo desde el panel.
- **E4 — Segunda empresa-cliente:** un tenant real distinto a Punto Rojo, validando aislamiento y flags en
  producción.

**Dependencias:** M1. **Criterio de cierre:** **SaaS completo** — segunda empresa operando con su set de flags
y cobro activo. **Riesgo:** medio.

---

## Fase 17 — Escala (futuro, no priorizado)

Extraer servicios (IA, facturación) a procesos propios; instancias de DB dedicadas; multi-bodega; analítica;
integración WhatsApp; **Habeas Data (Ley 1581)** cuando haya empresas externas; hardware POS.

---

## Camino crítico (resumen)

```
7 ─► 8 ─► 9 ─► 11 ─► 12 ─► 13 ─► 14 ─► 15 (M1: Punto Rojo operando) ─► 16 (M2: SaaS completo)
              └► 10 (asíncrono DIAN) ── paraleliza con 11/12
```

- **Prerequisitos duros:** 8 antes de 11/12/15; 9 antes de 11; 13 antes de 14/15.
- **Riesgos de bloqueo externo:** Fase 10 y el formato de montos dependen del **sandbox MATIAS**; agendar esa
  confirmación temprano aunque la fase se haga después.
- **Decisión de modelado pendiente más cara:** brechas §8 (Fase 8) — afectan ETL y facturación. Tratarlas como
  checkpoints con ADR.

## Primer paso concreto al retomar

Arrancar **Fase 7** (merge + guardarraíles): es de bajo riesgo, desbloquea `main` y cierra la deuda que ya
ocultó un bug. En paralelo, agendar la confirmación del **sandbox MATIAS** para no llegar a la Fase 10 a ciegas.
