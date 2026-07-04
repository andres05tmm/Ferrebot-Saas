# Profundización 2026 — diagnóstico y patrones (contable · agentes IA · frontend)

> Síntesis de la investigación de julio 2026 que fundamenta el plan de profesionalización
> (rama `feat/profesionalizacion-2026`). Complementa `benchmarking-competidores.md` (mercado)
> con profundidad **técnica**: qué existe hoy en el código, qué falta, y qué patrones de la
> industria se adoptan. Las decisiones puntuales van saliendo como ADRs 0023–0030 al construir.

## 1. Diagnóstico del código real

### 1.1 Contable

**Fortalezas (no tocar):** todo el dinero es `Numeric/Decimal` con redondeo único en
`core/money.py::cuantizar`; TZ Colombia centralizada; ventas insertan venta + detalle +
`MovimientoInventario` SALIDA y descuentan stock en una transacción con `FOR UPDATE`;
idempotencia por `idempotency_key` UNIQUE; fiados como ledger cargo/abono con lock de fila
ancla; arqueo híbrido documentado (ventas efectivo se leen de `ventas`, egresos de
`caja_movimientos` — ADR 0022 D1).

**Gaps (el plan los ataca en fases 2–5 y 8):**

| Gap | Evidencia |
|---|---|
| Sin doble partida / PUC / diario / períodos / cierres | grep exhaustivo: 0 referencias; reportes derivados al vuelo (`modules/reportes/repository.py`) |
| Anulación = borrado físico (solo mismo día) | `modules/ventas/service.py::borrar_venta`; enum `nota_credito` y tabla `notas_electronicas` existen pero nadie las emite |
| Devoluciones sin flujo | `mov_inventario_tipo` tiene `DEVOLUCION` sin uso |
| COGS por último precio de compra | snapshot `costo_unitario = producto.precio_compra` al vender; NULL cuenta 0 → margen inflado |
| Sin retenciones (retefuente/ICA/reteIVA) ni INC | `Venta` solo subtotal/impuestos/total con IVA único incluido |
| P&L mezcla criterios de fecha | ingresos por `Venta.fecha`, COGS por `MovimientoInventario.creado_en` |
| Conciliación bancaria ausente | tabla `bancolombia_transferencias` huérfana |
| 7 tablas huérfanas sin ORM | `notas_electronicas`, `documentos_soporte`, `eventos_dian`, `iva_saldos_bimestrales`, `libro_iva`, `cuentas_cobro`, `bancolombia_transferencias` (creadas en `0001_tenant_init`) |
| Idempotencia replay-only | solo `compras` detecta key igual + payload distinto → 409 (deuda FF-1) |
| Gastos pobres | enum fijo, sin vínculo a proveedor/CxP |

### 1.2 Agentes IA

**Fortalezas:** bypass determinista → dispatcher único (saneamiento → RBAC → capacidad →
Pydantic strict → rieles R1/R2/R3) → handlers; límites de monto fail-closed (`ai/limites.py`);
handoff + inbox completos (`TabConversaciones`, SSE); evals de function-call accuracy +
aislamiento + replay con corpus real y categoría PELIGROSO (meta 0); costo diario medido
(`core/llm/medicion.py`).

**Gaps:**
- **P0-1:** `core/llm/providers/claude.py::generate` hace una sola llamada sin retry → un 429/5xx
  transitorio tumba el turno a fallback. La factory ya es multi-proveedor pero sin fallback cableado.
- **P0-2:** `apps/wa/agent.py::ejecutar_runtime` NO pasa por `ai/saneamiento.revisar` — el canal
  público (WhatsApp) es el único sin malla anti-injection (solo Pydantic).
- **P1:** evals no cubren la ruta LLM (`replay --route llm` sin cablear) ni los packs WA ni el
  handoff; sin rate-limit por tenant/usuario; el presupuesto de costo contabiliza pero no corta.
- **P2:** sin métricas de agente (tasa handoff/fallback, p95, tokens/conversación); writer de
  `memoria_entidades` diferido en la ruta bot.

### 1.3 Frontend (`dashboard/`)

**Fortalezas:** 25+ tabs con tests Vitest+RTL; SSE robusto con backoff y evento `reconnected`;
gating por features; shadcn/Radix + Tailwind; manejo global de errores.

**Gaps:** JSX sin TypeScript; `useFetch` casero sin caché/dedupe/optimistic; offline/PWA
documentado (ADR 0004, `docs/offline-sync.md`) con 0 código; POS sin atajos de teclado ni foco
para lector de barras; backend sin UI: cobros Bold `/cobros`, cotizaciones, postventa,
reservas (sin REST propio), config de cadencia de cobranza; stubs `/kardex` y
`/facturas-recibidas`.

## 2. Patrones de la industria adoptados

### 2.1 Ledger de doble partida (fase 8)

Fuentes: Odoo `account.move`/`account.move.line`, ERPNext GL Entry + Perpetual Inventory,
Modern Treasury «How to Scale a Ledger», Square «Books».

- Asiento (cabecera) + líneas con **`direction` debit/credit y `amount` sin signo**; suma de
  débitos = suma de créditos validada en **app-layer** antes de postear.
- **Append-only:** un asiento `posted` no se edita — se **reversa** con un asiento espejo
  (`reverso_de`). Estados `pending` → `posted`.
- **Períodos contables** `open/locked/closed`; período bloqueado rechaza postings.
- **Saldos cacheados** por cuenta/período, recomputables desde las líneas (patrón Square Books).
- Un único modelo de asiento con enum de origen (venta/nota/gasto/manual), estilo `move_type`.
- **Asientos generados desde eventos operativos** por un proyector idempotente (un evento → un
  asiento; clave del evento = `idempotency_key` del asiento). El ledger es **capa derivada**:
  no alimenta el arqueo híbrido de caja; se concilian por reporte.
- PUC colombiano como árbol (`parent_id`, solo hojas imputables), configurable por tenant.
- Inventario: **costo promedio ponderado móvil** (patrón ERPNext Moving Average) antes que FIFO.

### 2.2 Tabla-stakes del mercado contable colombiano (Alegra/Siigo, 2026)

PUC, IVA bimestral, retenciones, libros auxiliar/mayor, estado de resultados + balance,
conciliación bancaria, documento equivalente POS electrónico (obligatorio 2026), nómina
electrónica (ambos la tienen — para nosotros **integración futura, no construir**).

### 2.3 Agentes LLM nivel producción

Fuentes: guía de soporte de Anthropic, «Building Effective Agents», OTel GenAI semconv, Langfuse.

- Single-agent + 4–5 tools acotadas (ya se cumple); gates deterministas ante acciones con
  dinero (ya existen: rieles + límites). El multi-agente solo para subtareas independientes.
- Retry con backoff+jitter solo ante errores transitorios; fallback de proveedor una vez.
- **Prompt caching** (`cache_control: ephemeral` en system + catálogo estable por tenant):
  hasta −90 % de costo y −85 % de latencia en prompts largos; no romper el prefijo.
- Presupuesto/rate-limit por tenant que **corta** (no solo contabiliza), con contador atómico.
- Evals en CI por tarea (function-call accuracy) + LLM-as-judge para texto libre.
- Observabilidad con métricas de agente: tasa de handoff, fallback, p95, tokens/conversación.

### 2.4 Frontend POS/dashboard

- **TanStack Query** para caché/dedupe/optimistic updates (patrón cancel→snapshot→update→
  invalidate), conviviendo con `useFetch` sin big-bang: lo nuevo o tocado migra.
- **TypeScript gradual** con `allowJs: true` (tipar primero `lib/api`), zod + react-hook-form
  solo en formularios nuevos.
- Atajos de teclado completos en el POS (operación sin mouse) + captura de lector de barras.
- Offline-first (IndexedDB + service worker + cola con `idempotency_key`) queda como fase
  opcional final — el backend ya soporta la idempotencia que exige.

## 3. Plan derivado

Fases y detalle completo en el plan aprobado (tracks paralelos):

- **Track agentes:** F0 seguridad P0 (retry + saneamiento WA) → F1 gobierno (rate-limit,
  caching, evals LLM, métricas).
- **Track contable:** F2 costo/datos (COGS promedio, ORM huérfanas, fechas P&L) → F3 notas
  crédito/devoluciones · F4 retenciones/libros · F5 conciliación/CxP → F8 ledger + PUC.
- **Track frontend:** F6 base (TanStack, TS gradual, atajos POS) → F7 pantallas faltantes.
- F9 opcional: offline/PWA.

ADRs a producir al construir: 0023 resiliencia LLM/saneamiento · 0024 gobierno de agentes ·
0025 COGS promedio · 0026 notas/devoluciones · 0027 retenciones/libros · 0028 conciliación ·
0029 estrategia frontend · 0030 ledger + PUC.

**Fuera de alcance:** nómina electrónica propia, multi-moneda, marketplace, capital.
