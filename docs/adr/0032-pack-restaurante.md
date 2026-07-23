# ADR 0032 — Pack Restaurante (paridad Yuumi)

- **Estado:** ACEPTADO — aprobado por Andrés en el checkpoint F0 (2026-07-23)
- **Fecha:** 2026-07-23
- **Relacionados:** ADR 0016 (`pack_pedidos`), ADR 0021 (features finas), ADR 0022 (cobro→venta),
  ADR 0014 (documento por venta), ADR 0025 (COGS), ADR 0007 (manifiesto), ADR 0011 (anti-alucinación)
- **Fixture de referencia:** `docs/fixtures/carta-siriuss/carta.yaml` (carta real de Siriuss)

## Contexto

El vertical restaurante necesita lo que `pack_pedidos` declaró v2 y lo que ningún pack cubre hoy:
pedido→venta, modificadores ("sin cebolla", proteína del plato del día), mesas/salón, comandas KDS,
menú QR público y recetas (BOM). Todo entra como datos + motor determinista + flags, sin tocar el
runtime (principio del ADR 0016). El esquema tenant es compartido por TODOS los verticales →
migraciones aditivas y NULL-safe; tabla vacía no cuesta.

## D1 — Flags y dependencias

| Flag | Cubre | Dependencia (OR) |
|---|---|---|
| `pack_mesas` | mesas, orden abierta, precuenta, cobro con propina | `ventas` |
| `kds` | zonas de comandas, vista cocina, estados por comanda | `pack_pedidos` o `pack_mesas` |
| `menu_qr` | página pública del menú por slug + QR | `ventas` |
| `recetas` | BOM por producto, descuento de insumos, costo del plato | `inventario` |

Sin meta-pack nuevo: el manifiesto de un restaurante lista las finas (igual que servicios, ADR 0021).
`pack_pedidos` no cambia de dependencia. Los cuatro entran en `OPCIONALES` + `DEPENDENCIAS` de
`core/tenancy/catalogo.py` y en `docs/feature-flags.md`.

## D2 — Impuestos: `tipo_impuesto` por producto (impoconsumo 8%)

**Decisión de Andrés (no re-abrir): el INC 8% SE MODELA.** Hoy `productos.iva` es un `SmallInteger`
con la tarifa (0/5/19) y `ventas_detalle.iva` la snapshotea. Cambio mínimo y aditivo:

- `productos.tipo_impuesto TEXT NOT NULL DEFAULT 'iva'` — valores `'iva' | 'inc'`. La columna
  `iva` pasa a leerse como **tarifa del impuesto que sea** (para INC: 8). Ferreterías no cambian nada.
- `ventas_detalle.tipo_impuesto TEXT NOT NULL DEFAULT 'iva'` — snapshot al vender (mismo patrón que
  el precio).
- Reportes: el Libro IVA y los saldos bimestrales filtran `tipo_impuesto='iva'` (el INC no es IVA
  descontable); el INC se agrega aparte cuando haga falta reporte fiscal (fuera de alcance del MVP,
  que es sin fiscal real).
- Los precios de carta se tratan como **precio final al público** (impuesto incluido), igual que el
  POS hoy — pendiente confirmar con la DUDA D4 del fixture.

Alternativa descartada: tabla de impuestos aparte (sobrediseño para 2 tipos; el catálogo de
`retenciones` ya cubre lo paramétrico tributario).

## D3 — Modificadores de menú

Catálogo relacional + snapshot JSONB (la espec objetivo es la estructura del fixture Siriuss):

- **`modificador_grupos`**: `producto_id FK`, `nombre` ("Proteína"), `min_sel`, `max_sel`,
  `obligatorio`, `orden`, `activo`.
- **`modificador_opciones`**: `grupo_id FK CASCADE`, `nombre` ("Carne asada"), `delta_precio MONEY
  DEFAULT 0`, `activo`.
- **Snapshot en el ítem**: `pedido_items.modificadores JSONB NULL` — lista
  `[{grupo, opcion, delta_precio}]` al momento del pedido (el catálogo puede cambiar; el pedido no —
  mismo principio que nombre/precio). Sin tabla puente: el snapshot es write-once y se lee entero
  (KDS, conversión a venta); JSONB evita 2 tablas y N+1.
- **Motor**: `armar_pedido` acepta `modificadores` por ítem; valida contra el catálogo
  (min/max/obligatorio), suma `delta_precio` al precio del ítem, total determinista. Modificador
  inexistente → error recuperable con sugerencias (riel R1/R2: el bot pregunta, nunca inventa).
- **Combos fijos** (Menú especial): un producto normal cuyos componentes van en la descripción; NO
  se modela elección (INFERENCIA I3). Si mañana hay combos con elección, son grupos modificadores.
- **Menú del día rotativo** (INFERENCIA I4): se resuelve con `productos.activo` +
  `modificador_opciones.activo` — apagar sin borrar. Ya existe; sin esquema nuevo.
- Conversión a venta: los modificadores viajan en `ventas_detalle.descripcion`
  ("Plato fuerte — Carne asada, sin cebolla") y el delta ya viene sumado en `precio_unitario`.

## D4 — Mesas y orden abierta: REUSAR `pedidos` (con `mesa_id`)

- **`mesas`**: `nombre`, `zona TEXT NULL` ("terraza"), `activo`. CRUD admin.
- La orden abierta ES un `Pedido` con `origen='mesa'`, `mesa_id FK NULL` y estado nuevo
  **`abierto`** (valor añadido al enum `pedido_estado`, aditivo): `abierto → confirmado(cobrado) |
  cancelado`. Mientras está `abierto` se agregan ítems por rondas (append, no reemplazo — a
  diferencia del borrador `recibido` de WhatsApp, que se reemplaza). UNIQUE parcial: una sola orden
  `abierta` por mesa.
- **Precuenta** = render del pedido abierto (total en vivo); no muta nada.
- **Cobro** = puente F1 (pedido→venta) con propina opcional (D7). Al cobrar: venta idempotente,
  pedido a estado final, mesa liberada.
- Concurrencia (2 meseros): agregar ítems toma el pedido con `SELECT … FOR UPDATE`; el cobro usa el
  candado del puente F1.

**Trade-off vs entidad nueva (`ordenes_mesa`)**: una entidad aparte evita mezclar semánticas de
estado, pero duplica ítems/snapshot/SSE/conversión-a-venta y obliga a un segundo puente a venta.
Reusar `pedidos` da KDS gratis (F4 lee pedidos confirmados de ambos orígenes) y un solo puente F1.
El costo (estado `abierto` que los flujos de domicilio nunca usan) se acota validando transiciones
por `origen`. **Decisión (Andrés, checkpoint F0): REUSAR `pedidos`.**

## D5 — Comandas KDS

- **`comanda_zonas`**: `nombre` ("parrilla", "bar"), `activo`. Ruteo: `productos.zona_comanda_id
  FK NULL` — producto sin zona → zona default "cocina" (implícita, no fila).
- **`comandas`**: `pedido_id FK`, `zona_id FK NULL`, `estado ('pendiente'|'en_preparacion'|'listo')`,
  timestamps por transición (auditoría). **`comanda_items`**: `comanda_id FK CASCADE`,
  `pedido_item_id FK`, cantidad. Un pedido confirmado con ítems de 2 zonas → 2 comandas.
- Vista `/kds` (dashboard, flag `kds`): columnas por zona, SSE (`useRealtime` existente), pantalla
  siempre-encendida v1 simple (riesgo A.4: reconexión ya la maneja el hook).
- "Listo" (todas las comandas del pedido listas) → notificación al canal del pedido (puerto de
  envío existente por origen; mock en tests).

## D6 — Menú digital QR (público)

- Ruta pública `GET /publico/{slug}/menu` (API) + página estática del dashboard (sin auth, sin JS
  del dashboard privado): resuelve el tenant POR SLUG (control DB), lee SOLO catálogo activo
  (nombre, precio, modificadores activos, secciones por `categoria`, branding). Test de aislamiento
  explícito: la respuesta de un slug jamás contiene datos de otro tenant ni campos internos
  (costos, stock, proveedores).
- Deep-link a WhatsApp (`wa.me/{numero}?text=...`) con el número del canal del tenant.
- QR generado en el dashboard (lib QR ya disponible en el stack del front o SVG server-side).

## D7 — Propina (solo salón/mostrador)

**Decisión de Andrés (no re-abrir): propina solo en salón/mostrador, NUNCA domicilio, siempre
opcional y elegida por el cliente al pagar.** Modelado v1 = **línea varia** en la venta
(`producto_id=None`, `descripcion='Propina'`, `iva=0`, `descontar_stock=False`) — patrón ADR 0022
D2, cero esquema nuevo. Queda discriminada por descripción, suma al total y por tanto
`ventas_efectivo` cuadra el arqueo sin código nuevo. El endpoint de cobro de mesa/mostrador acepta
`propina` opcional; el de conversión de domicilio NO la expone (guardarraíl por construcción).

## D8 — Recargo de domicilio POR PLATO (caso real Bocagrande)

`zonas_domicilio.recargo_por_item MONEY NOT NULL DEFAULT 0` (aditivo; tarifa plana sigue igual).
`confirmar_pedido`: `costo_domicilio = tarifa + recargo_por_item × Σ cantidades`. Bocagrande =
tarifa base (DUDA D5) + `recargo_por_item=1000`. Sin efectos colaterales: la columna nueva con
default 0 deja idénticas las zonas existentes.

## D9 — Recetas (BOM) e insumos

- **`recetas`**: `producto_id FK` (el plato), `insumo_id FK` (producto del catálogo con
  inventario), `cantidad NUMERIC(12,3)` (compatible con fracciones). UNIQUE(producto, insumo).
- Al convertir pedido/mesa en venta: producto CON receta → movimientos SALIDA de **cada insumo**
  (cantidad × cantidad vendida), NINGÚN movimiento del plato (el plato no lleva stock). Producto sin
  receta → comportamiento actual intacto (regresión cero en ferretería).
- Costo del plato = Σ (costo_promedio del insumo × cantidad) — reusa COGS (ADR 0025).
- **Insumo insuficiente: ALERTA, no bloquea** (política de Andrés, ratificar aquí): la venta pasa,
  el stock del insumo queda negativo-visible y se emite el aviso interno (patrón `pack_pagar`).

## Baselines F0 (registradas)

- Suite completa (CI de `c89e6b7`, tag `pre-pack-restaurante`): **2257 passed, 1 skipped** + 43 evals.
- Replay ferretería: **400/586 = 68.3% acierto, 0 peligrosos** —
  `tests/evals/replay/baseline/baseline_puntorojo.json`. Gate de TODAS las fases: ≥ 68.3% y 0
  peligrosos.

## DUDAS del fixture (D1–D5) e INFERENCIAS (I1–I4) — RESUELTAS (checkpoint F0, 2026-07-23)

| Id | Pregunta | Resolución (Andrés) |
|---|---|---|
| D1 | ¿Cuántos acompañantes incluye el plato fuerte? | **2** (`min=1, max=2`) |
| D2 | ¿La sopa está incluida en el plato fuerte o siempre aparte? | Aparte (tiene precio propio $14.000) |
| D3 | ¿Bebida incluida en el plato del día? | No incluida |
| D4 | ¿Precios de carta con o sin INC 8%? | Precio final al público (INC incluido) — como el POS hoy |
| D5 | ¿Tarifa base de domicilio (solo se ve Bocagrande +$1.000/plato)? | `costo_domicilio_default` del tenant; Bocagrande vía D8 |
| I1 | 10 proteínas = grupo min=1 max=1 delta 0 | Aceptada |
| I2 | 4 acompañantes incluidos, delta 0 | Aceptada |
| I3 | Menú especial = combo fijo (sin elección) | Aceptada |
| I4 | Menú del día rotativo → activar/desactivar sin borrar | Aceptada (cubierto con `activo`, D3) |

## Consecuencias

- 4 migraciones tenant aditivas nuevas a lo largo de F1–F6 (modificadores, mesas+estado, comandas,
  recetas+tipo_impuesto+recargo) — todas NULL-safe con defaults; los demás verticales no ven cambio.
- `restaurante-demo` (plantillas-verticales) suma los 4 flags nuevos y catálogo con modificadores
  de la carta Siriuss (F7).
- El puente pedido→venta (F1) cierra la deuda declarada del ADR 0016 y sirve a domicilio Y salón.
