# ADR 0017 — `pack_ventas`: cotizaciones y carrito por WhatsApp (hacia el cliente final)

- **Estado:** aceptado (11 jun 2026)
- **Contexto:** `docs/plan-impulso-agentes-2026.md` §2.5 (Ola 2 #6): el cerebro que ya vende *hacia
  adentro* (vendedor por Telegram) apuntado *hacia afuera* (cliente final por WhatsApp).

## Contexto

"¿A cómo el bulto de cemento? ¿Tienes drywall?" — la consulta de precio es la conversación entrante
más frecuente de una ferretería/distribuidor. El activo más maduro del proyecto ya resuelve esto por
dentro: buscador de 4 capas (exacta→alias→trigram→fuzzy), precios escalonados por cantidad
(`obtener_precio_para_cantidad`) y fracciones. Este pack lo expone al cliente final con guardarraíles.

## Decisión

### Capa 1 — Datos (migración tenant `0020_cotizaciones`)

- **`ventas_wa_config`** (una fila, get-or-create): `mostrar_stock` (si el agente puede decir
  cuántas unidades hay), `vigencia_dias` (default 3) de la cotización emitida.
- **`cotizaciones`**: `cliente_telefono` (identidad = el que escribe), estado
  (`abierta → emitida → aceptada | vencida | cancelada`), total, `vigencia_hasta`, idempotencia.
- **`cotizacion_items`**: snapshot de nombre y precio (la cotización emitida NO cambia aunque el
  catálogo cambie — por eso tiene vigencia).

### Capa 2 — Motor (`modules/cotizaciones/service.py`, determinista)

- `cotizar(texto, cantidad)` — resuelve contra el catálogo y calcula el precio con el motor REAL de
  precios (escalonado por cantidad incluido). Stock solo si `mostrar_stock`.
- `agregar(telefono, items)` / `quitar` / `ver` — UN carrito (`abierta`) por teléfono; agregar el
  mismo producto actualiza la línea (recotiza el precio por la nueva cantidad).
- `emitir(telefono)` — cierra el carrito: `emitida` + `vigencia_hasta = hoy + vigencia_dias` + SSE.
- `marcar(id, aceptada|cancelada)` — dashboard. Las `emitida` vencidas pasan a `vencida` al listar
  (barrido perezoso, sin cron).
- **Cotización → venta: v2.** Aceptarla no toca stock ni caja (regla #7); el negocio la convierte
  en venta POS por el flujo normal.

### Capa 3 — Herramientas (`ai/cotizaciones_tools.py`, flag `pack_ventas`)

`cotizar_producto(producto, cantidad?)` · `agregar_a_cotizacion(items)` ·
`quitar_de_cotizacion(producto)` · `ver_mi_cotizacion` · `emitir_cotizacion`.

**Guardarraíl clave (del plan):** el agente **nunca inventa precio ni stock** — solo herramientas;
si el producto no resuelve, ofrece sugerencias del buscador o escala. Teléfono SOLO del `Contexto`.

### Cableado

- Flag `pack_ventas`, **requiere `pos`** (cotiza el catálogo del POS).
- Sección en el system prompt compuesto; router `/api/v1/cotizaciones` (staff lee y marca; config
  admin). Pestaña React: pendiente (la página del plan §4 no la define; v1 opera por API y el
  resumen de la cotización viaja por el chat).

## Alternativas / pendientes

- **PDF de la cotización:** v2 (v1 = resumen formateado por chat, suficiente para cerrar la venta).
- **Precio mayorista por cliente identificado** (`clientes.telefono` match): anotado para v1.1 —
  exige decidir cómo se autoriza a un teléfono como mayorista (no automático: riesgo de filtrar
  precios negociados).

## Consecuencias

- La ferretería que ya compró el POS obtiene "la versión hacia el cliente" del mismo producto, con
  ~60% de consultas resolubles por bypass (costo LLM casi cero) cuando se cablee el bypass v2.
