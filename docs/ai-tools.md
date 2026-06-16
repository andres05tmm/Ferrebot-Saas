# Herramientas IA y bypass

> Contrato de las herramientas (function calling) y especificación del camino rápido (`bypass`).
> Decisión en `adr/0003-ia-hibrida-bypass-function-calling.md`. Esquema de datos en `schema.md`; permisos en `auth-rbac.md`; capacidades en `feature-flags.md`.

## 1. Principios

- **La IA no ejecuta lógica de negocio.** El modelo solo **decide qué herramienta llamar** con qué argumentos. La herramienta es código determinístico que valida permisos, resuelve la empresa, calcula totales/impuestos, mueve stock/caja y deja auditoría.
- **El modelo nunca toca la base.** Toda lectura/escritura pasa por la capa de servicios → repositorios de la empresa resuelta (`get_tenant_db()`). Ver `tenancy.md`.
- **Agnóstico de proveedor.** La capa de herramientas (`ai/tools.py`) no depende de OpenAI ni de Claude. Cada proveedor recibe el mismo catálogo traducido a su formato (`tools`/`functions`). El despachador es uno solo.
- **Dos caminos, una sola lógica.** `bypass` (Python puro) y function calling terminan llamando **al mismo servicio de dominio**. El bypass no reimplementa reglas: solo evita el modelo cuando la instrucción es inequívoca.
- **Idempotencia siempre.** Toda herramienta que mueve dinero o stock exige `idempotency_key` (ver §4). Reintentos del bot, de la cola offline o de webhooks no duplican.

## 2. Arquitectura del agente (un turno)

```
mensaje (texto / voz→Whisper)
      │
      ▼
┌─────────────────┐   match     ┌──────────────────────────┐
│   BYPASS router │ ──────────▶ │ servicio de dominio        │ ──▶ resultado
│ (regex+catálogo)│             │ (ventas/caja/...)          │
└─────────────────┘             └──────────────────────────┘
      │ no-match / ambiguo                    ▲
      ▼                                        │ misma capa
┌─────────────────┐  tool_call   ┌────────────┴────────────┐
│  MODELO (LLM)   │ ───────────▶ │  DESPACHADOR de tools    │
│ Haiku/Sonnet    │ ◀─────────── │  (ai/tools.py)           │
└─────────────────┘  tool_result └──────────────────────────┘
      │ respuesta en lenguaje natural
      ▼
   usuario  (+ evento SSE al dashboard de la empresa)
```

- **Selección de modelo** (ver `.claude/rules/performance.md`): **Haiku** para clasificación/operación frecuente; **Sonnet** cuando el turno requiere razonar varios pasos o desambiguar. **Opus** no se usa en runtime del bot.
- **Contexto del turno (RAG, sin memoria permanente):** se arma desde la base de la empresa en cada turno con `conversaciones_bot` (historial reciente por `chat_id`), `memoria_entidades` (último cliente/producto mencionado), `memoria_turno` y `price_cache`. Nada de estado del modelo entre turnos.
- **Costo:** cada llamada al modelo registra tokens en `api_costo_diario` (por empresa).

## 3. Contrato común (envelope)

Toda herramienta recibe y devuelve un sobre uniforme. El modelo solo ve `args`; el resto lo inyecta el despachador desde el contexto del request.

**Contexto inyectado (no lo provee el modelo):**

```json
{
  "tenant_id": 1,
  "usuario_id": 42,
  "rol": "vendedor",
  "origen": "bot",                 // web | bot | voz | offline
  "idempotency_key": "uuid-v4",    // generado por el cliente/bot
  "request_id": "uuid-v4"
}
```

**Resultado (lo que la herramienta devuelve al modelo y al canal):**

```json
{
  "ok": true,
  "data": { },                     // payload específico de la herramienta
  "resumen": "Venta #1234 por $45.000 registrada.",  // texto para el usuario
  "evento": "venta_registrada",    // evento SSE emitido (o null)
  "idempotente": "aplicada"        // aplicada | duplicada | null
}
```

**Error:**

```json
{
  "ok": false,
  "error": "stock_insuficiente",
  "detail": "Quedan 3 de 'cemento gris 50kg', se pidieron 5.",
  "recuperable": true              // true = el modelo puede repreguntar/ajustar
}
```

### Códigos de error comunes

| Código | Significado | `recuperable` |
|---|---|---|
| `producto_no_encontrado` | La búsqueda no resolvió un producto único | sí |
| `producto_ambiguo` | Varios candidatos; requiere elegir | sí |
| `stock_insuficiente` | Stock < cantidad (no aplica a venta offline; ver `offline-sync.md`) | sí |
| `cliente_no_encontrado` | Fiado/abono sin cliente válido | sí |
| `caja_cerrada` | Operación que requiere caja abierta | sí |
| `permiso_denegado` | El `rol` no alcanza (ver `auth-rbac.md`) | no |
| `capacidad_no_habilitada` | Feature flag off (p. ej. facturación) | no |
| `validacion` | Argumentos inválidos (Pydantic) | sí |
| `idempotencia_conflicto` | Misma key, payload distinto | no |
| `limite_excedido` | Supera un límite por empresa y requiere un rol superior (`limite_modo=escalar`) | no |
| `error_interno` | Fallo no esperado | no |

## 4. Idempotencia, RBAC y capacidades en las herramientas

- **Idempotencia:** `registrar_venta`, `registrar_gasto`, `registrar_compra`, `registrar_fiado`, `abonar_fiado`, `emitir_factura` validan `idempotency_key` (UNIQUE en su tabla) **antes de insertar**. Si la key existe con el mismo payload → `idempotente: "duplicada"` y se devuelve el resultado original; si existe con payload distinto → `idempotencia_conflicto`.
- **RBAC:** cada herramienta declara su **rol mínimo**; el despachador lo verifica con el `rol` del contexto. Coincide con la matriz de `auth-rbac.md`.
- **Capacidades:** las herramientas fiscales (`emitir_factura`, notas) verifican la flag (`require_feature`) de la empresa; si está off → `capacidad_no_habilitada`. Si una empresa no tiene la capacidad, la herramienta **ni se expone** al modelo en ese tenant.

## 5. Catálogo de herramientas

Resumen (contrato detallado abajo). "Bypass" = la operación tiene camino rápido sin modelo.

| Herramienta | Rol mín. | Muta | Idempotente | Evento SSE | Feature | Bypass |
|---|---|---|---|---|---|---|
| `buscar_producto` | vendedor | no | — | — | núcleo | sí |
| `consultar_stock` | vendedor | no | — | — | núcleo | sí |
| `consultar_cliente` | vendedor | no | — | — | núcleo | sí |
| `crear_cliente` | vendedor | sí | no¹ | — | núcleo | no |
| `registrar_venta` | vendedor | sí | sí | `venta_registrada` | núcleo | **sí** |
| `registrar_gasto` | vendedor | sí | sí | `gasto_registrado` | núcleo | sí |
| `registrar_compra` | admin | sí | sí | `compra_registrada`, `inventario_actualizado` | núcleo | no |
| `registrar_fiado` | vendedor | sí | sí | — | `fiados` | sí |
| `abonar_fiado` | vendedor | sí | sí | — | `fiados` | sí |
| `abrir_caja` | vendedor | sí | no² | `caja_abierta` | núcleo | sí |
| `cerrar_caja` | vendedor | sí | no² | `caja_cerrada` | núcleo | sí |
| `consultar_caja` | vendedor | no | — | — | núcleo | sí |
| `emitir_factura` | vendedor | sí | sí | `factura_pendiente` | `facturacion_electronica` | no |
| `generar_reporte` | vendedor³ | no | — | — | núcleo | sí |

¹ Dedup por `documento` (no por key). ² Naturalmente idempotente por el índice parcial "una caja abierta por vendedor". ³ `resultados`/`libro_iva` requieren `admin`.

> Mapeo herramienta → servicio → endpoint REST equivalente en §8. Las herramientas son la cara conversacional de los mismos servicios que sirven la API (`api-contract.md`).

### 5.1 buscar_producto

Resuelve texto libre ("cemento gris", "tornillo 1/4") a uno o varios productos. Búsqueda fuzzy + aliases + FTS (índice trigram sobre `productos.nombre`).

```json
// args
{ "consulta": "cemento gris 50", "limite": 5 }
// data
{ "items": [
  { "id": 88, "codigo": "7700001", "nombre": "Cemento gris 50kg",
    "precio_venta": 28000, "precio_mayorista": 26000, "iva": 19,
    "stock_actual": 12, "permite_fraccion": false, "score": 0.94 }
], "ambiguo": false }
```

Si hay empate de score (varios candidatos plausibles) → `ambiguo: true` y el modelo (o el flujo bot) pregunta cuál.

### 5.2 consultar_stock

```json
// args  (uno de los dos)
{ "producto_id": 88 }            // o
{ "consulta": "cemento gris", "solo_bajo": false }
// data
{ "producto_id": 88, "nombre": "Cemento gris 50kg",
  "stock_actual": 12, "stock_minimo": 5, "bajo": false }
```

### 5.3 consultar_cliente

```json
// args
{ "consulta": "Ferretería La 80", "documento": null }
// data
{ "id": 12, "nombre": "Ferretería La 80", "documento": "900123456",
  "tipo_documento": "NIT", "saldo_fiado": 150000, "regimen": "comun" }
```

### 5.4 crear_cliente

```json
// args
{ "nombre": "Juan Pérez", "tipo_documento": "CC", "documento": "1088...",
  "telefono": "300...", "correo": null, "direccion": null,
  "ciudad_dane": null, "regimen": null }
// data
{ "id": 57, "creado": true }   // creado=false si ya existía por documento
```

`ciudad_dane` y `regimen` solo se piden si la empresa tiene `facturacion_electronica` (campos fiscales condicionales, ver `feature-flags.md`).

### 5.5 registrar_venta  (núcleo, idempotente, con bypass)

El modelo **nunca** envía totales: el backend calcula `subtotal`, `impuestos` (IVA por ítem) y `total`. `producto_id` null = venta varia (ítem libre por `descripcion`).

```json
// args
{
  "items": [
    { "producto_id": 88, "cantidad": 2, "precio_unitario": null },
    { "producto_id": null, "descripcion": "corte de lámina", "cantidad": 1, "precio_unitario": 5000 }
  ],
  "metodo_pago": "efectivo",       // efectivo|transferencia|tarjeta|nequi|daviplata|fiado
  "cliente_id": null,              // requerido si metodo_pago = fiado
  "facturar": false                // si true y feature on → encola emisión tras la venta
}
// data
{ "venta_id": 1234, "consecutivo": 1234, "subtotal": 61000,
  "impuestos": 11590, "total": 72590, "metodo_pago": "efectivo",
  "factura_id": null }
```

Reglas: valida stock (online); `precio_unitario` null toma el precio del producto (mayorista si aplica feature `mayorista` y el flujo lo indica); inserta `ventas` + `ventas_detalle` + `movimientos_inventario` (SALIDA) **en una sola transacción**; si `metodo_pago = fiado` crea el `fiados` asociado (requiere feature `fiados` + `cliente_id`). Offline: no rechaza por stock (ver `offline-sync.md`).

### 5.6 registrar_gasto

```json
// args
{ "categoria": "transporte", "monto": 15000, "concepto": "flete proveedor" }
// data
{ "gasto_id": 77, "caja_movimiento_id": 301 }   // egreso en caja_movimientos
```

Requiere caja abierta del vendedor → si no, `caja_cerrada`.

### 5.7 registrar_compra  (admin)

```json
// args
{ "proveedor_id": 9,
  "items": [ { "producto_id": 88, "cantidad": 50, "costo": 22000 } ],
  "fiscal": null }   // { "proveedor_nit", "base", "iva", "soporte_url" } si compras_fiscal
// data
{ "compra_id": 45, "total": 1100000, "entradas_inventario": 1 }
```

Genera ENTRADA de inventario por cada ítem (misma transacción). El bloque `fiscal` solo se acepta con feature `compras_fiscal`.

### 5.8 registrar_fiado / abonar_fiado  (feature `fiados`)

```json
// registrar_fiado args
{ "cliente_id": 12, "venta_id": 1234, "monto": 72590 }
// abonar_fiado args
{ "cliente_id": 12, "monto": 50000 }   // o fiado_id
// data (abono)
{ "fiado_id": 5, "abono": 50000, "saldo_nuevo": 22590 }
```

El saldo del cliente se recalcula desde `fiados_movimientos` (cargo/abono); nunca se escribe a mano.

### 5.9 abrir_caja / cerrar_caja / consultar_caja

```json
// abrir_caja args
{ "saldo_inicial": 100000 }
// cerrar_caja args
{ "saldo_contado": 540000 }    // data: { saldo_esperado, diferencia }
// consultar_caja → data
{ "caja_id": 20, "estado": "abierta", "saldo_inicial": 100000,
  "ingresos": 460000, "egresos": 20000, "saldo_esperado": 540000 }
```

`abrir_caja` falla si ya hay una abierta para el vendedor (índice parcial `UNIQUE(usuario_id) WHERE estado='abierta'`).

### 5.10 emitir_factura  (feature `facturacion_electronica`)

No es síncrona: **encola** la emisión DIAN (ARQ) y reserva consecutivo. Ver `facturacion-dian.md`.

```json
// args
{ "venta_id": 1234, "tipo": "factura" }   // factura | documento_soporte
// data
{ "factura_id": 900, "estado": "pendiente", "consecutivo": "FE-1024",
  "encolada": true }
```

El usuario consulta el avance con `consultar_factura` (o el dashboard recibe `factura_aceptada`/`factura_rechazada` por SSE).

### 5.11 generar_reporte

```json
// args
{ "tipo": "ventas", "periodo": "diario", "formato": "texto" }
// tipo: ventas|resultados|top_productos|libro_iva ; formato: texto|excel
// data (texto)
{ "titulo": "Ventas de hoy", "total": 1250000, "num_ventas": 38,
  "lineas": ["Efectivo: $900.000", "Transferencia: $350.000"] }
```

`resultados` y `libro_iva` requieren `admin`; `libro_iva` además requiere la feature. `formato: "excel"` genera el archivo (vía la ruta de reportes) y devuelve un enlace.

## 6. Especificación del bypass (camino rápido sin modelo)

**Objetivo:** resolver el ~60% de operaciones inequívocas en Python puro, en <5 ms, sin gastar tokens ni latencia de modelo. El bypass **clasifica e interpreta superficialmente**; la lógica de negocio sigue en el servicio.

### 6.1 Cuándo aplica

El router de bypass intenta `match` **antes** de invocar al modelo. Aplica solo si:

1. El mensaje encaja con un **patrón conocido** (intención + entidades extraíbles sin ambigüedad).
2. Cada entidad **resuelve a un único objeto** (p. ej. el producto encontrado tiene `score` alto y sin empate).
3. La operación es de **bajo riesgo o reversible** según política (ver §6.4).

Si cualquiera falla → **fallback** al modelo (function calling), que puede repreguntar.

### 6.2 Patrones (intents)

Reglas léxicas (regex + diccionario de unidades/fracciones + aliases del catálogo). Ejemplos para español de mostrador:

| Intención | Ejemplos de entrada | Herramienta destino |
|---|---|---|
| venta simple | `2 cemento gris`, `vendí 3 tornillos 1/4 efectivo`, `1 martillo a 25000` | `registrar_venta` |
| consulta de stock | `cuánto hay de cemento`, `stock varilla 1/2` | `consultar_stock` |
| consulta de precio | `precio cemento gris`, `a cómo el bulto` | `buscar_producto` |
| gasto | `gasto transporte 15000`, `pagué flete 20mil` | `registrar_gasto` |
| abono a fiado | `abono Juan 50000`, `Juan pagó 50mil` | `abonar_fiado` |
| caja | `abrir caja 100000`, `cerrar caja 540000`, `cómo va la caja` | `abrir/cerrar/consultar_caja` |
| reporte rápido | `ventas de hoy`, `cuánto vendí hoy` | `generar_reporte` |

Soporta: cantidades con fracción (`1/2`, `1/4`, `medio`, `cuarto`), montos coloquiales (`20mil`, `20k`, `$20.000`), método de pago explícito (`efectivo`, `nequi`, `fiado`), y aliases por empresa (typos frecuentes → producto).

### 6.3 Algoritmo

```
bypass(mensaje, ctx):
  intent, slots = clasificar(mensaje)          # regex + diccionarios
  if intent is None: return FALLBACK
  entidades = resolver(slots, ctx.tenant)      # producto/cliente vía repos
  if entidades.ambiguo or entidades.faltan:    # >1 candidato / falta dato
      return FALLBACK                          # el modelo desambigua
  if not politica_permite(intent, ctx):        # riesgo/rol/feature
      return FALLBACK
  return despachar(intent, entidades, ctx)     # MISMO servicio que el LLM
```

- `resolver` usa los mismos repositorios que `buscar_producto`/`consultar_cliente`.
- `despachar` llama al servicio de dominio con el contexto (idempotencia, RBAC, capacidades incluidos). **No hay rama de lógica duplicada.**
- El bypass **no inventa precios ni totales**: pasa cantidades/ítems; el servicio calcula.

### 6.4 Política (qué nunca hace solo el bypass)

Caen siempre al modelo (o piden confirmación), aunque el patrón encaje:

- **Anulación de venta**, notas crédito/débito, ajustes de inventario (riesgo alto).
- **Emisión de factura** si hay datos fiscales incompletos del cliente.
- Venta a **fiado** sin cliente resuelto a uno solo.
- Cualquier monto/cantidad por encima de un **umbral configurable** por empresa → confirma o escala (ver «Política de límites» abajo).
- Mensajes con **múltiples intenciones** en una frase (p. ej. "vende 2 cemento y registra gasto 10mil").

#### Política de límites (`ai/limites.py`) — el límite vive en la herramienta, no en el permiso

El RBAC dice QUÉ rol puede; los límites dicen CUÁNTO. El despachador aplica una capa SEPARADA del
cálculo de negocio (sobre `registrar_venta` hoy; lista para `anular_venta`/descuentos cuando se
expongan), configurable por empresa vía `config_empresa` (control DB, `tools.set_config`):

| Clave | Tipo | Efecto |
|---|---|---|
| `venta_monto_max` | decimal | Tope del total de una venta (vacío = sin tope). |
| `venta_descuento_max_pct` | decimal | % de descuento máximo por línea (precio efectivo vs. catálogo). |
| `limite_modo` | `confirmar`\|`escalar` | Al exceder: pedir confirmación explícita (default) o exigir un rol superior. |
| `limite_rol_minimo` | rol | Rol que puede exceder cuando `limite_modo=escalar` (default `admin`). |

`confirmar` → corta con un "¿Confirmo?" y el "sí" del mismo turno reusa la `idempotency_key` (no
duplica). `escalar` → si el rol no alcanza, devuelve `limite_excedido` (no recuperable) y no ejecuta;
un rol ≥ `limite_rol_minimo` sí puede. Sin claves configuradas no hay tope (no cambia el comportamiento).

### 6.5 Confirmación

Para operaciones que mutan dinero/stock vía bypass, el bot puede pedir confirmación de una línea ("Venta 2× Cemento gris = $66.640. ¿Confirmo? sí/no") según `config_empresa.bypass_confirmar` (por empresa). La confirmación reusa la `idempotency_key` ya generada, así un "sí" no duplica.

### 6.6 Observabilidad del bypass

Cada turno registra: `ruta` (`bypass` | `modelo`), `intent`, `latencia_ms`, y si hubo fallback y por qué (`ambiguo`, `politica`, `no_match`). Métricas por empresa: **tasa de bypass** (meta ~60%), tasa de fallback por causa, latencia p50/p95. Sirve para afinar patrones y aliases.

## 7. Voz (Whisper)

Audio → transcripción (`audio_logs`) → **mismo pipeline** (bypass primero, luego modelo). Ventas por voz con confirmación pendiente se guardan en `ventas_pendientes_voz` hasta que el usuario confirma. Requiere features `bot_telegram` + `ventas_voz`. Filtros de ruido/entrada en `voz_filtros`.

## 8. Mapeo herramienta → servicio → API → evento

Las herramientas y los endpoints REST son **dos fachadas del mismo servicio**. Esto garantiza paridad de reglas entre bot, voz y dashboard.

| Herramienta | Servicio | Endpoint REST equivalente (`api-contract.md`) | Evento |
|---|---|---|---|
| `buscar_producto` | `productos` | `GET /productos?q=` | — |
| `consultar_stock` | `inventario` | `GET /inventario/stock` | — |
| `consultar_cliente` | `clientes` | `GET /clientes?q=` | — |
| `crear_cliente` | `clientes` | `POST /clientes` | — |
| `registrar_venta` | `ventas` | `POST /ventas` | `venta_registrada` |
| `registrar_gasto` | `gastos` | `POST /gastos` | `gasto_registrado` |
| `registrar_compra` | `compras` | `POST /compras` | `compra_registrada`, `inventario_actualizado` |
| `registrar_fiado` | `fiados` | `POST /fiados` | — |
| `abonar_fiado` | `fiados` | `POST /fiados/{id}/abono` | — |
| `abrir_caja` | `caja` | `POST /caja/apertura` | `caja_abierta` |
| `cerrar_caja` | `caja` | `POST /caja/cierre` | `caja_cerrada` |
| `consultar_caja` | `caja` | `GET /caja/actual` | — |
| `emitir_factura` | `facturacion` | `POST /facturacion/emitir` | `factura_pendiente` |
| `generar_reporte` | `reportes` | `GET /reportes/*`, `POST /reportes/excel` | — |

## 9. Pruebas (ver `.claude/rules/testing.md`)

- **Unitarias de clasificación:** corpus de frases reales de mostrador → intent/slots esperados (incluye typos, fracciones, montos coloquiales).
- **Paridad bypass ↔ modelo:** la misma entrada por ambos caminos produce el **mismo efecto** en la base.
- **Idempotencia:** repetir una `registrar_venta`/`emitir_factura` con la misma key no duplica.
- **Fallback:** entradas ambiguas no mutan nada por bypass (caen al modelo).
- **Aislamiento:** una herramienta ejecutada para empresa A nunca lee/escribe en la base de B.
- **Capacidades/RBAC:** herramienta fiscal con feature off → `capacidad_no_habilitada`; rol insuficiente → `permiso_denegado`.
