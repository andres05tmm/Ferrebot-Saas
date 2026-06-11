# ADR 0016 — `pack_pedidos`: pedidos y domicilios por WhatsApp

- **Estado:** aceptado (11 jun 2026)
- **Contexto:** `docs/plan-impulso-agentes-2026.md` §2.3 (prioridad alta de la Ola 2) y §4 (página
  Pedidos del dashboard: kanban en vivo). Mismo patrón de pack que agenda (ADR 0006) y cobranza
  (ADR 0015): datos + motor determinista + herramientas acotadas al teléfono + tab + flag.

## Contexto

El pedido es la conversación de WhatsApp más natural de Colombia ("me mandas 2 hamburguesas y una
coca-cola"). El 87% de las mipymes vende por WhatsApp y nadie pequeño en Cartagena lo tiene bien
resuelto. El pack apunta a restaurantes/tiendas/ferreterías con domicilio, **reusando el catálogo
real del POS** (`productos` + `inventario` + el buscador de 4 capas exacta→alias→trigram→fuzzy).

## Decisión

### Capa 1 — Datos (migración tenant `0019_pedidos`)

- **`pedido_config`** (una fila, get-or-create): `activo`, `hora_apertura`/`hora_cierre` (horario de
  cocina), `minimo_pedido`, `tiempo_estimado_min`, `costo_domicilio_default`.
- **`zonas_domicilio`**: barrio → tarifa (`nombre`, `tarifa`, `activo`). Barrio sin zona → tarifa default.
- **`pedidos`**: cabecera con `cliente_telefono` (identidad = el número que escribe), dirección,
  zona, `estado` (`recibido → confirmado → en_preparacion → en_camino → entregado | cancelado`),
  subtotal/total, `idempotency_key` (única parcial), origen.
- **`pedido_items`**: snapshot de nombre y precio al momento (el precio del catálogo puede cambiar
  después; el pedido no).

### Capa 2 — Motor (`modules/pedidos/service.py`, determinista)

- `armar_pedido(telefono, items)` — resuelve cada ítem contra el catálogo con `BuscadorProductos`
  (el agente NUNCA inventa productos ni precios); valida horario de cocina y stock disponible
  (informativo de inventario: **no descuenta stock** — regla #7: el stock solo cambia con movimiento,
  y eso ocurre cuando el negocio convierta el pedido en venta); calcula subtotal con `precio_venta`.
  Un borrador (`recibido`) por teléfono: volver a armar lo reemplaza. Idempotente por `idempotency_key`.
- `confirmar_pedido(telefono, direccion, barrio, metodo_pago)` — exige borrador, valida
  `minimo_pedido`, resuelve la tarifa de domicilio por zona (o default), pasa a `confirmado` y emite
  SSE (`pedido_confirmado`) → la cocina lo ve en el kanban al instante.
- `estado_de(telefono)` — el último pedido del que escribe (acotado a SU teléfono).
- `cambiar_estado(id, nuevo)` — dashboard: solo transiciones válidas del ciclo; `cancelado` solo
  desde estados no finales. Emite `pedido_estado`.
- **Cobro v1:** `metodo_pago` es una etiqueta (efectivo/transferencia/datáfono). El cobro real
  (link/QR Bre-B) llega con el frente de pagos (ADR 0013) — el motor ya guarda el método.
- **Pedido → venta/documento equivalente:** v2. El kanban opera el ciclo; convertirlo en venta POS
  (descuenta stock, mueve caja, emite POS electrónico) se hará desde el dashboard cuando se cablee
  con la venta — requiere vendedor/caja y NO es necesario para operar el canal.

### Capa 3 — Herramientas (`ai/pedidos_tools.py`, flag `pack_pedidos`)

`ver_menu(buscar?)` · `armar_pedido(items, notas?)` · `confirmar_pedido(direccion, barrio?,
metodo_pago, nombre?)` · `estado_mi_pedido()`. Teléfono SOLO del `Contexto` (guardarraíl idéntico a
agenda/cobranza); `escalar_humano` ya es núcleo. Errores recuperables con sugerencias del buscador
("no encontré 'hamburguza', ¿quisiste decir Hamburguesa?").

### Cableado

- Flag `pack_pedidos`, **requiere `pos`** (el menú ES el catálogo del POS).
- Sección de pedidos en el system prompt compuesto; intro propia si el tenant no tiene agenda.
- Router `/api/v1/pedidos` (staff opera el kanban; config/zonas admin) + **TabPedidos** kanban en
  vivo (SSE) — "LA pantalla del restaurante".

## Consecuencias

- El pedido no toca stock ni caja: cero riesgo fiscal/contable hasta que el negocio lo convierta.
- Vertical nuevo (restaurantes) sin tocar el runtime: solo datos + motor + herramientas + flag
  (métrica M-producto del plan se mantiene).
