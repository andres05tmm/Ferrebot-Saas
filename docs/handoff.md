# Handoff — FerreBot SaaS

> Documento de traspaso entre sesiones (Cowork / Claude Code). Léelo al retomar para no perder el hilo.
> Última actualización: cierre de Fase 6 (facturación DIAN síncrona), commit `c47f1ce`.

## Rol y flujo de trabajo

El asistente de **Cowork** actúa como **copiloto de arquitectura y revisión**. Andrés ejecuta en
**Claude Code** (otra ventana); Cowork revisa las entregas, decide con él y le **redacta los prompts**.

Ciclo por entregable:

```
RED (tests que fallan + esqueletos)  →  Cowork revisa
GREEN (implementación)               →  Cowork verifica leyendo archivos/diff
commit                               →  (merge lo hace Andrés)
```

En lo delicado, Cowork hace un **checkpoint con recomendación antes del RED**. Cowork **no ejecuta** en
el repo; verifica leyendo archivos. **Git no resuelve desde el sandbox de Cowork** (si hace falta comparar
contra `main`, Andrés pasa el diff).

## Carpetas

| Carpeta | Qué es |
|---|---|
| `C:\Users\Dell\Documents\Claude\Projects\ferrebot-saas` | **NUEVO** — POS multi-tenant (SaaS). Empezar por `CLAUDE.md` + `docs/`. |
| `C:\Users\Dell\Documents\GitHub\bot-ventas-ferreteria` | FerreBot **ORIGINAL** single-tenant — fuente a portar (contrato MATIAS, lógica de negocio). |

## Estado del proyecto

- **Fases 1-4:** cerradas (núcleo multi-tenant, dominio ventas/caja/fiados/clientes/inventario/memoria, auth/RBAC, eventos SSE).
- **Fase 5 (bot Telegram):** cerrada y **mergeada** a `main` (merge `991db09`, `--no-ff`).
- **Fase 6 (facturación DIAN/MATIAS):** cerrada y **mergeada** a `main` (merge `d5e232d`, `--no-ff`).
- **Suite:** 330 tests, 0 fallos, 0 errores (con Postgres + Redis arriba). Incluye guardarraíles de Fase 7.

**`main` creado e integrado** (fase-1 → fase-6) con ambos merge commits visibles, suite verde en cada paso.
**Remote:** `https://github.com/andres05tmm/Ferrebot-Saas.git` (privado) — `main` pusheado.

- **Fase 7 (consolidación + guardarraíles):** **CERRADA**. E1 merge a `main`; E2 smokes HTTP de los 4
  routers núcleo (`tests/test_smoke_routers_http.py`); E3 `/ready` comprueba control DB + Redis (503 si
  falla); E4 caché de `MatiasClient` por tenant en el worker. **Suite: 330 verdes, 0 fallos.** Ver
  `docs/fase-7-consolidacion/plan.md`.
- **Fase 8 (cierre de esquema del tenant):** **CERRADA** — y resultó mínima: la migración `0001` **ya creaba
  las 35 tablas** de `schema.md`, así que no hubo "construir esquema". Quedó en: E1 modelo ORM `Alias` (tabla
  ya existía); E3 drop del `config_empresa` **vestigial** del tenant (migración `0005`) + quitar su seed muerto
  en `provision_tenant` (la config no-secreta vive en el control DB, decisión D2); E2 guardarraíl de paridad
  de esquema (`tests/test_schema_paridad.py`, 34 tablas en head). Suite **334 verdes**. Decisiones diferidas a
  momento-ETL (Fase 15): zona horaria G4, gasto→caja, proveedores desde texto, subtotal/impuestos históricos.
  Ver `docs/fase-8-esquema/plan.md`.
- **Fase 9 (feature flags efectivas + `GET /config`):** **CERRADA**. E1 catálogo canónico de capacidades +
  validación de dependencias (`core/tenancy/catalogo.py`, modo OR); E2 caché TTL de efectivas
  (`capacidades_cache.py`, espeja `ControlCache`) + endurecimiento de tests (fixture autouse que limpia los
  singletons); E3 `GET /api/v1/config` (`modules/config/router.py`: features = núcleo ∪ efectivas + branding +
  usuario; `leer_branding` en `control_repo`, default `#C8200E`); E4 guardarraíl de montaje de rutas. Suite
  **~352 verdes**. Ver `docs/fase-9-features/plan.md`.
- **Fase 11 (dashboard web MVP white-label):** **CERRADA**. Dashboard React (Vite+Tailwind+shadcn) servido
  por la API (`StaticFiles dashboard/dist` + catch-all SPA; `TenantMiddleware` solo exige tenant en `/api/`).
  - **Backend:** `POST /auth/login` (Telegram Login Widget → JWT; **401** firma inválida / **403** no
    autorizado) + `GET /auth/me`; endpoints núcleo `clientes` (CRUD+dedup), `GET /ventas` (lista/historial con
    scoping RBAC `get_filtro_efectivo`) + `GET /ventas/{id}` con líneas, `GET /reportes/resumen`; catálogos
    fiscales `GET /clientes/ciudades|paises` (MATIAS por empresa, gate `facturacion_electronica` → 404).
  - **Frontend:** auth (Telegram Widget + `useAuth` + Bearer/401 centralizado en `lib/api.js` + `ProtectedRoute`);
    tiempo real (UN solo stream SSE por `RealtimeProvider` + `useRealtimeEvent`, fetch-based con
    `@microsoft/fetch-event-source`); theming runtime `--color-primary` desde branding; gating de tabs por
    `/config` (nombres de `catalogo.py`); **7 tabs núcleo** recableados (Hoy, Ventas rápidas, Inventario, Caja,
    Gastos, Clientes, Historial).
  - **E7 cierre:** `tests/test_e2e_dashboard.py` (login→resumen→SSE→venta→resumen, lifespan vivo) +
    `test_spa_serving` con dist real + `docs/fase-11-dashboard/smoke-manual.md`. Suite **~395 verdes** +
    dashboard **43 Vitest**. Ver `docs/fase-11-dashboard/plan.md`.
  - **Diferido a Fase 12:** CRUD de inventario (crear/editar/eliminar, fracciones, mayorista), tabs fiscales
    completos (Facturación, Libro IVA, Compras fiscal, Proveedores, FE recibidas, Kárdex), reportes pesados
    (Resultados, Top productos), `VistaMes` rica (heatmap), selector de vendedor para admin.
  - **Unknown a confirmar:** host de `/countries` de MATIAS (el original usaba `api-v2.matias-api.com`; el SaaS
    lo cuelga del `base_url` por-tenant) — validar contra el sandbox MATIAS. `venta_anulada` aún no se publica
    (no hay anulación todavía); el front ya se suscribe a ese evento.
- **Diferido a Fase 13:** `PUT /admin/empresas/{id}/features` + auth `super_admin` (cross-tenant) +
  invalidación de caché de capacidades (hoy solo TTL). `validar_dependencias` ya existe pero aún sin consumir
  (lo usará ese endpoint). + smoke de provisioning (cero cobertura).
- **Drift de docs menor:** `api-contract.md` pone el chequeo de dependencias en `/health`, pero está en
  `/ready` (`/health` es liveness). Reconciliar en docs cuando se toque.
- **Nota transporte SSE en tests:** httpx `ASGITransport` bufferiza la respuesta hasta completarla → no se
  puede leer un SSE infinito por HTTP en pytest; el E2E suscribe `event_hub` directamente (mismo bus que
  `/api/v1/events`) y el SSE HTTP real se valida en el smoke manual.
- **Próximo:** Fase 12 (amplitud facturación + tabs fiscales/reportes del dashboard). O Fase 10 (asíncrono
  DIAN), que paraleliza pero necesita el sandbox MATIAS — agendarlo.

### Cadena de Fase 6 (facturación síncrona, completa y probada de punta a punta)

```
E1   núcleo UBL puro (mapas verbatim, math IVA incluido, pre-checks FAU04/FAX14)
E2   MatiasClient por empresa (auth JWT, /invoice, /cities; httpx perezoso)
E3   persistencia + servicio (crear_pendiente / emitir; estados pendiente|aceptada|error)
E4a  política de reintento/dead-letter (pura) + categoria en el parser de E2
E4b-1 emitir dirigido por la política + estado rechazada
E4b-2 worker ARQ (job emitir_documento + loader de config MATIAS)
E4e  endpoint POST /api/v1/facturas (encola) + gate require_feature
RC-1 cableado de runtime (get_capacidades, get_facturacion_service, pool ARQ, on_startup)
RC-2 smoke E2E: API encola → worker ARQ → MATIAS (mock) → pendiente→aceptada
```

## Deuda diferida (ver `docs/fase-6-facturacion/NOTAS-ENTREGABLES.md`)

- **E4c reconciliador + E4d webhook** — resolución **asíncrona** del estado DIAN. Diferidos porque el
  contrato de `/status` y `/documents` **no está fijado en la fuente** (el original devuelve raw y trata
  el CUFE síncrono como verdad). Confirmar contra el **sandbox MATIAS** antes de construir.
- **Formato de montos** (number vs string en el payload) — emitimos `number` (espejo del original);
  confirmar contra el sandbox.
- **Smokes HTTP de los routers existentes** (ventas/caja/inventario/fiados) — su ausencia ocultó el bug
  de `get_tenant_db` sin anotar `request: Request`. Agregar como guardarraíl.
- **Resto de facturación sin portar:** documento soporte (DS-NO), notas crédito/débito, eventos RADIAN,
  libro IVA, compras/compras fiscal, proveedores (Cloudinary), honorarios.

## Infraestructura local

- Contenedores corriendo: `ferrebot-pg` (Postgres, `localhost:5433`) y `ferrebot-redis` (Redis, `localhost:6379`).
- PG13 nativo **detenido** (liberó el 5433); en arranque manual.
- Tras un reinicio: Redis levanta solo (`--restart unless-stopped`); **Postgres necesita `docker start ferrebot-pg` manual**.
- Los tests de integración/E2E requieren **ambos** arriba.

## Reglas no negociables

- SQL **solo en repos** (sesión del tenant; el control DB se lee con sesión **per-call**).
- **Multi-tenancy:** sin `empresa_id` en tablas de negocio (la base es la frontera); resolver siempre el tenant antes de consultar.
- **Secretos cifrados por empresa** en el control DB; nunca con SQL del tenant, nunca hardcode/env global.
- `async`/`await` donde hay IO; clientes httpx/Redis **perezosos** (sin red al importar/construir).
- Hora **Colombia** (`now_co`/`today_co`); **Decimal** puro en dinero.
- Funciones <50 líneas; type hints 3.10+; docstrings en español; `get_logger`, nunca `print`.
- Commits `tipo: descripción`, **sin atribución**. Tests: `.venv/Scripts/python.exe -m pytest`.

## Próxima decisión grande (roadmap) — DECIDIDA

Estimado: **~45-55% del proyecto** hecho. Lo hecho es lo más duro (cimientos backend + bot + emisión
síncrona); falta **amplitud**: dashboard, resto de facturación, operación real.

**Objetivo decidido con Andrés (sesión de planeación):** **doble** — *Punto Rojo operando* **y**
*SaaS completo con dashboard*. Comparten casi todo el camino crítico; se separan solo al final en dos
hitos (M1 Punto Rojo operando, M2 SaaS completo).

**Plan de fases detallado → `docs/roadmap.md`** (Fases 7-17, con entregables, dependencias, criterios de
cierre y riesgos). Resumen del tronco:

```
7 Consolidación ─► 8 Esquema (brechas §8) ─► 9 Feature flags+/config ─► 11 Dashboard MVP ─►
12 Amplitud facturación ─► 13 Provisioning ─► 14 Deploy+DR ─► 15 ETL Punto Rojo (M1) ─► 16 SaaS (M2)
                       └► 10 Asíncrono DIAN (paraleliza)
```

**Primer paso al retomar:** arrancar **Fase 7** (merge `feat/fase-5-bot` + `feat/fase-6-facturacion` a
`main` + smokes HTTP + `/health`). En paralelo, agendar confirmación del **sandbox MATIAS** (necesario
para Fase 10 y el formato de montos).
