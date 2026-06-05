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
- **Suite:** 318 tests, 0 fallos, 0 errores (con Postgres + Redis arriba).

**`main` creado e integrado** (fase-1 → fase-6) con ambos merge commits visibles, suite verde en cada paso.
Verificado: `get_tenant_db(request)` intacto, lifespan con `arq_pool`, `/health`+`/ready` montados.
**Pendiente:** el repo **no tiene remote** — todo es local. Considerar crear uno (GitHub) para respaldo.
**Fase 7 en curso:** E1 (merge) hecho; faltan E2 smokes HTTP, E3 `/ready` real, E4 caché MatiasClient
(ver `docs/fase-7-consolidacion/plan.md`).

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
