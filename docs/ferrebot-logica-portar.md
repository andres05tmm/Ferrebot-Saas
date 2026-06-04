# Lógica de FerreBot a portar (notas para Claude Code)

> Extracto anotado del código real de FerreBot (`bot-ventas-ferreteria/`) para reimplementarlo en el monolito modular multi-tenant. **No copiar verbatim:** FerreBot es single-tenant, con caché global en memoria y acceso a datos mezclado en `memoria.py`. Aquí queda el **comportamiento** (reglas, fórmulas, contratos de API externas) que debe preservarse; la arquitectura nueva está en `architecture.md`/`system-design.md`.
>
> Referencias `archivo:línea` son del repo FerreBot, para abrir el original cuando haga falta. Complementa `ai-tools.md` (contratos destino), `facturacion-dian.md` y `migracion-puntorojo.md`.

## 0. Mapa de archivos FerreBot → módulos destino

| FerreBot (origen) | Qué tiene | Módulo destino |
|---|---|---|
| `bypass.py` | camino rápido sin IA (ventas/fracciones) | `ai/bypass.py` |
| `ai/tools.py` | schemas de tool-calling (4+1 tools) | `ai/tools.py` |
| `ai/__init__.py` (≈71k) , `ai/response_builder.py` | loop del modelo + ejecutor de 19 tags | `ai/agent.py` + servicios de dominio |
| `ai/prompts.py`, `ai/prompt_products.py`, `ai/prompt_context.py` | system prompt + catálogo compacto | `ai/prompts/` |
| `ai/price_cache.py`, `ai/semantic_catalog.py` | caché de precios / catálogo semántico | `ai/` (caché → Redis, ver §9) |
| `services/catalogo_service.py` | precios, fracciones, búsqueda catálogo | `modules/inventario` (precios) |
| `services/inventario_service.py` | stock, descuento transaccional, alertas | `modules/inventario` |
| `services/facturacion_service.py` (≈1.3k) | MATIAS/DIAN factura | `modules/facturacion` |
| `services/documento_soporte_service.py` | DS-NO (CUDE, resolución propia) | `modules/facturacion` (DS) |
| `services/eventos_dian_service.py` | eventos DIAN | `modules/facturacion` |
| `services/caja_service.py` | caja diaria + gastos | `modules/caja` |
| `services/fiados_service.py` | saldos y movimientos de fiado | `modules/fiados` |
| `services/honorarios_service.py` | cuentas de cobro | `modules/honorarios` |
| `services/search_service.py`, `fuzzy_match.py`, `alias_manager.py` | búsqueda FTS/trigram/fuzzy/alias | `modules/inventario` (búsqueda) |
| `ventas_state.py`, `routers/ventas.py` | máquina de estado de venta | `modules/ventas` |
| `routers/*` | endpoints (FastAPI ya existe) | `apps/api` + `modules/*/router.py` |
| `handlers/*` | bot Telegram (comandos + wizard) | `apps/bot` |

## 1. Arquitectura IA real (importante)

FerreBot **no** usa tool-calling para todo. Hoy:

- **Tool-calling nativo** solo para las **4 mutaciones de plata** (`registrar_venta`, `registrar_gasto`, `registrar_fiado`, `abonar_fiado`) + `crear_cliente` en voz (`ai/tools.py:199-208`). Es donde clasificar mal cuesta dinero.
- **Todo lo demás** (consultas, alta de cliente por bot, ajustes, facturación, reportes) va por **tags de texto** `[VENTA]{...}[/VENTA]`, `[GASTO]…`, etc., que un ejecutor de ~700 líneas y **19 tags** (`ai/response_builder.py::procesar_acciones`) interpreta.
- Hay un **puente** `tool_uses_a_tags()` (`ai/tools.py:471`) que convierte los `tool_use` de vuelta a tags para no reescribir el ejecutor. Está detrás del flag `config.IA_TOOL_CALLING`.

**Recomendación para el SaaS:** ir a **tool-calling para todo** (sin el puente de tags) — es la dirección que `ai-tools.md` ya documenta. El puente y los 19 tags son deuda heredada; portarlos tal cual replicaría la fragilidad que el propio código admite. Conservar las **reglas** de cada tag, no su formato.

### Reglas de los tools (de `ai/tools.py`, conservar)

- `registrar_venta` se invoca **una vez por producto** distinto del mensaje.
- `total` es el **total de la línea** (lo que pagó el cliente por ese ítem), **nunca** el precio unitario; sin `$` ni comas.
- `precio_declarado: true` **solo** si el vendedor dijo un monto explícito ("a cinco mil", "= 8000"). Si lo omite, el **catálogo es la fuente de verdad** del precio.
- `producto` = nombre limpio del catálogo **sin** la fracción; la fracción va en `cantidad` (0.25 = ¼).
- `metodo_pago`/`cliente` solo si se mencionan. Enum de pago en FerreBot: `efectivo|transferencia|datafono` (el SaaS amplía a nequi/daviplata/tarjeta).

### Rieles de validación de voz (de `ai/tools.py:303-468`, muy útil)

Antes de registrar lo que dijo el modelo, FerreBot valida:
1. **Producto desconocido** (`ventas_con_producto_desconocido`): si el `producto` no existe en catálogo (salvo "Venta Varia"), no registra → pregunta. Evita inventar productos.
2. **Precio dudoso** (`ventas_con_precio_dudoso`): si no hubo `precio_declarado` y el `total` del modelo difiere del catálogo (precio×cantidad) más de **1% (mín. 1 peso)**, no registra → pregunta. Evita alucinación de precios.
3. **Confirmación hablada** de gasto/fiado/abono antes de ejecutar.

> Portar estos rieles al SaaS como parte del despachador de tools (encajan con la "política de bypass" de `ai-tools.md` §6.4).

## 2. Bypass (camino rápido) — de `bypass.py`

Cabecera del archivo documenta el contrato (`bypass.py:1-30`). Resultado medido: **~60% de mensajes**, **800ms → <5ms**.

**Qué bypassa** (ventas simples):
1. Cantidad entera: `2 martillo` → `2 × precio_unidad`.
2. Fracción sola: `1/2 vinilo azul t1` → `precios_fraccion["1/2"]`.
3. Fracción mixta: `1-1/2 vinilo` → `precio_unidad×1 + precios_fraccion["1/2"]`.
4. Mixta en texto: `1 y medio vinilo`.
5. Entero múltiple: `3 vinilo`.

**Qué lo deshabilita** (cae al modelo) — listas de palabras en `bypass.py:41-66`:
- **Cliente/crédito:** `fiado, a nombre, cuenta de, credito, factura, abono, debe, saldo, deuda`; y `para <Nombre propio>` (regex `bypass.py:53`, sobre el texto original con mayúsculas).
- **Consulta:** `cuanto, vale, precio, hay, stock, queda, inventario, reporte, total, gasto, ultimo…`.
- **Modificación:** `cambia, quita, agrega, borra, corrige, cancela, olvida…`.
- **Multi-producto** (comas/saltos de línea), **fracción inexistente** en catálogo, y **productos con `precio_por_cantidad`** (mayorista por umbral) → siempre al modelo.

**Mapa de fracciones** (`bypass.py:73-105`): numéricas `1/16,1/8,1/4,3/8,1/2,3/4` y escritas `medio/media, cuarto/un cuarto, tres cuartos, un octavo`. Patrones mixtos: `N-1/2`, `N y medio`, `N 1/2`.

**Normalización** (`bypass.py:111`): minúsculas + sin tildes/ñ; lija `#120 → n120`; fracciones se preservan como `1_4` antes de limpiar especiales (`_slug`).

**Math fracción mixta** es determinista (suma exacta en Python) — su valor es que el modelo nunca se equivoca en aritmética.

> En el SaaS, el bypass debe llamar al **mismo servicio de ventas** que el tool-calling (sin lógica duplicada, ver `ai-tools.md` §6.3). El umbral de monto/confirmación pasa a `config_empresa`.

## 3. Modelo de precios — de `services/catalogo_service.py`

Un producto puede tener **tres** esquemas de precio (conviven):

1. **Precio simple:** `precio_unidad` (entero pesos).
2. **Precio por cantidad / mayorista** (`precio_por_cantidad`): `{ umbral, precio_bajo_umbral, precio_sobre_umbral }`. Si `cantidad >= umbral` aplica `precio_sobre_umbral`, si no `precio_bajo_umbral`. **Estos son los `productos.precio_umbral/bajo_umbral/sobre_umbral`** del esquema (ver `migracion-puntorojo.md`).
3. **Fracciones** (`precios_fraccion` / tabla `productos_fracciones`): `{ "1/4": {decimal:0.25, precio:N}, … }`.

Algoritmo central `obtener_precio_para_cantidad(nombre, cantidad) -> (total, precio_unidad)` (`catalogo_service.py:315`):

```
prod = buscar(nombre)
si prod tiene precio_por_cantidad:
    pu = precio_sobre_umbral if cantidad>=umbral else precio_bajo_umbral
    return round(pu*cantidad), pu
si alguna fracción coincide (|decimal - cantidad| < 0.01):
    return precio_de_esa_fracción, precio_unidad
return round(precio_unidad*cantidad), precio_unidad
```

Decisión de Punto Rojo (tenant #1): **se conserva este modelo tal cual** (umbral + fracciones). El esquema destino debe incluir `productos_fracciones` y los tres campos de umbral (brecha §8 de `migracion-puntorojo.md`). Otros tenants podrán usar solo `precio_unidad`.

## 4. Búsqueda de productos (3 capas)

Orden real de resolución:

1. **Exacta/normalizada** sobre catálogo (`buscar_producto_en_catalogo`, normaliza `nombre_lower`, plurales, fracciones).
2. **FTS + trigram en Postgres** (`services/search_service.py`): full-text primero; si devuelve poco, **suplementa con `similarity()`** (pg_trgm) con **umbral 0.3** para tolerar typos (`drwayll→drywall`). Aplica a productos, conversaciones y ventas.
3. **Fuzzy conservador** (`fuzzy_match.py`, `rapidfuzz.token_sort_ratio`): solo se activa cuando la búsqueda exacta da `None`. Umbral **92%** para **sugerir** (no asumir); 80-91% se descarta a propósito (productos distintos con precio distinto). Además exige ≥1 palabra común >3 letras (evita `martillo→tornillo`).
4. **Aliases** (`alias_manager.py`, tabla `aliases` + `productos.aliases[]`): variantes/typos persistentes (`/alias`). `ventas_detalle.alias_usado` registra cuál se usó.

> Dependencias destino: `pg_trgm` (índice GIN/trigram en `productos.nombre`, ya previsto en `schema.md`) + `rapidfuzz` en requirements. La búsqueda fuzzy **sugiere y pide confirmación**, no auto-resuelve.

## 5. Facturación DIAN / MATIAS — de `services/facturacion_service.py`

API **MATIAS v3.0.0**. Auth `https://auth-v2.matias-api.com` (email+password → **JWT renovable**); base `https://api-v2.matias-api.com/api/ubl2.1`. Credenciales por env en FerreBot; en el SaaS van **cifradas por empresa** (`secretos_empresa`).

**REGLA DE ORO (v3)** (`facturacion_service.py:15-18`): en **GET** se usan **códigos DIAN** (CC=13, NIT=31, CE=22…); en **POST** (creación) se usan **IDs internos** de MATIAS (CC=1, NIT=3, CE=2…). No confundirlos: es fuente típica de rechazos.

**`city_id` ≠ DANE** (`facturacion_service.py:160-194`): MATIAS usa IDs internos de ciudad. Se carga un **caché DANE→matias_id** desde `GET /cities` (`_cargar_ciudades_matias` / `_matias_city_id(dane)`). `clientes.municipio_dian` guarda el código; default **149 = Cartagena**. En el SaaS: caché **por empresa** (`facturacion-dian.md`).

**Consecutivo** (`_siguiente_num_dian`, `facturacion_service.py:359`): `MAX(parte numérica de numero) + 1` con `LOCK TABLE facturas_electronicas IN SHARE ROW EXCLUSIVE MODE`, piso en `MATIAS_NUM_DESDE`. **Un número enviado a DIAN no se reusa aunque sea rechazado** (no se filtra por estado). En el SaaS esto pasa a una **SEQUENCE por tenant** reservada al crear el `pendiente` (ver `facturacion-dian.md`); al migrar, `setval` al máximo real (`migracion-puntorojo.md` §6).

**Mapas a preservar:**
- Medios de pago → código MATIAS (`facturacion_service.py:45`): efectivo 10, transferencia/nequi/daviplata 42, tarjeta/datafono 48.
- Unidad de medida → `quantity_units_id` (`_UNIDAD_DIAN`): Unidad 70, Galón 686, … (verificados contra `GET /quantity-units`).
- `resolution_number = MATIAS_RESOLUTION`, `MATIAS_PREFIX` (FPR en prod).

**Documento Soporte (DS-NO)** — servicio aparte `services/documento_soporte_service.py`:
- Usa **CUDE** (no CUFE), **resolución propia** `MATIAS_RESOLUTION_DSNO` y consecutivo propio `_siguiente_num_ds` (mismo patrón de LOCK + regex, piso `MATIAS_DS_NUM_DESDE`).
- En endpoint DS, `identity_document_id="3"` genera `schemeName="31"` en el XML (lo exige la DIAN para DS — fix DSAJ25a). Requiere `mobile` del proveedor (DSAJ08a).
- Se vincula a `cuentas_cobro` (FK `documentos_soporte.cuenta_cobro_id`). Decisión Punto Rojo: **DS es tabla aparte** (no plegado en facturas).

**Eventos DIAN:** `services/eventos_dian_service.py` + columnas `evento_030/031/032/033_at`, `evento_estado` en `compras_fiscal` (acuse de facturas de proveedor).

> En el SaaS la emisión es **asíncrona (ARQ) con reintentos y dead-letter** (hoy en FerreBot es más directa). Conservar payload, mapas y reglas de consecutivo; cambiar el *cuándo* (cola) no el *qué*.

## 6. Reglas de dominio (preservar)

**Ventas → inventario (transaccional)** — `inventario_service.py:280` `descontar_inventario_pg(cur, ...)`:
- Corre **dentro del cursor de la transacción** de la venta: si la venta falla, el descuento se revierte con el ROLLBACK.
- `SELECT … FOR UPDATE` sobre la fila de inventario (evita carreras entre ventas del mismo producto).
- Contrato de retorno **exacto**: `(bool, str|None, float|None)` = (encontrado, alerta_stock_bajo, cantidad_restante). `ventas_state.py:210` lo destructura.
- Alerta si `cantidad_nueva <= minimo`.
- **Factor "wayper"/kg** (`_resolver_wayper_inventario`): algunos productos se venden en una unidad pero descuentan otra (multiplicador).
- ⚠️ **Divergencia con el SaaS:** FerreBot **clampa a 0** (`max(0.0, …)`). El POS offline del SaaS **permite stock negativo con marca de revisión** (`offline-sync.md`). Decidir por tenant; online puede seguir validando, offline no rechaza.
- Tras commit, invalida caché de memoria (en el SaaS, invalidar caché Redis del tenant).

**Caja (modelo diario)** — `services/caja_service.py`:
- Una fila de `caja` por **día** (`abierta`, `monto_apertura`, `efectivo`, `transferencias`, `datafono`, `cerrada_at`).
- Esperado en efectivo = `monto_apertura + ventas_efectivo − gastos_de_caja` (`caja_service.py:153`).
- ⚠️ El SaaS modela **apertura/cierre con arqueo** (`saldo_contado`, `diferencia`) y **`caja_movimientos`** explícitos (`schema.md`). Es una **reconstrucción** de modelo, no copia (ver `migracion-puntorojo.md` §4.10).

**Fiados** — `services/fiados_service.py:70` `guardar_fiado_movimiento`:
- `saldo_nuevo = saldo_anterior + cargo − abono`.
- **Atómico:** UPSERT del saldo + INSERT del movimiento en la **misma transacción** (`fiados` + `fiados_movimientos`).
- El saldo del cliente se deriva de los movimientos (no se escribe suelto). Igual en el SaaS.

**Gastos:** en FerreBot `gastos` no está ligado a una `caja_id`; el SaaS exige que **todo gasto mueva caja** (`caja_movimientos` egreso). Reconstruir el vínculo (brecha §8).

## 7. Qué NO portar / cambiar en el SaaS

- **Keepalive / self-ping** (`keepalive.py`): eliminado → monitor de uptime externo (`infra-railway.md`).
- **Wompi / Bold** (`routers/wompi_webhook.py`, `bold_webhook.py`): removidos (`architecture.md` §12).
- **Caché global en memoria del proceso** (`memoria.py` ≈1.2k líneas, `cargar_memoria()`, `price_cache.py`, `_indice_nombres`): asume **un solo tenant y un solo proceso**. En el SaaS: caché **por empresa en Redis** (multi-instancia), nunca estado global de módulo. Es el refactor más grande.
- **Dinero como `integer`**: el SaaS usa `NUMERIC(12,2)` (`migracion-puntorojo.md` G2).
- **Tags de texto + puente** (`response_builder` 19 tags, `tools.py:471`): migrar a tool-calling nativo completo.
- **`ai/__init__.py` monolítico (≈71k)**: dividir por responsabilidad (loop, prompts, despachador) en la capa `ai/` nueva.
- **Acceso a datos con SQL suelto** en services/handlers: en el SaaS va **por repositorios** (regla no negociable #2).

## 8. Orden sugerido al portar (encaja con `task_list.md`)

1. **Catálogo + precios** (`catalogo_service` → `modules/inventario`): modelo de precio (umbral+fracciones) y `obtener_precio_para_cantidad`. Es la base de venta y bypass.
2. **Búsqueda** (exacta → FTS/trigram → fuzzy → alias).
3. **Ventas** + **inventario transaccional** (`descontar_inventario_pg`, contrato de retorno, FOR UPDATE).
4. **Bypass** llamando al servicio de ventas; luego **tool-calling** con los mismos servicios + rieles de validación.
5. **Caja / gastos / fiados** (reglas §6).
6. **Facturación**: MATIAS (auth, city_id, consecutivo, mapas) + DS-NO + eventos, ya **asíncrono (ARQ)**.
7. **Honorarios** (cuentas de cobro → DS).

> Para cada módulo: primero el **test de paridad** (misma entrada que FerreBot → mismo efecto), luego implementar (TDD, `.claude/rules/development-workflow.md`).
