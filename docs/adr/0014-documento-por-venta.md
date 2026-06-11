# ADR 0014 — El documento fiscal de una venta lo decide la capacidad del tenant + una intención por venta

> Estado: **Aceptado** (10 jun 2026). Generaliza el cierre POS automático (ADR 0012 D2) para soportar
> tenants **FE-only** y **no-fiscales** con un único punto de ruteo. El número 0013 queda reservado
> para Bre-B. Slice F2.3a (backend del ruteo; sin UX — persistir/elegir la intención en la UI es
> F2.3b/c).

## Contexto

ADR 0012 introdujo el **cierre POS automático por venta**: tras registrar la venta, el hook
`cerrar_venta_con_pos` (en `modules/facturacion/pos_hook.py`) crea un pendiente tipo `pos`, lo commitea
y encola la emisión. El gate era una sola capacidad: `if "pos_electronico" not in capacidades: return`.

Eso deja dos clases de tenant sin cierre fiscal:

- **FE-only** (`facturacion_electronica` ON, `pos_electronico` OFF): hoy solo factura a mano desde el
  dashboard; el mostrador no cierra ningún documento automáticamente.
- **No-fiscal** (sin capacidades fiscales): vende y lleva inventario/caja, sin documento DIAN — un caso
  legítimo (comercio no obligado, o tenant en período de prueba), no un error.

Además, ADR 0012 D1 ya define la **exclusión POS↔FE** (una venta cierra con UN documento; si el cliente
pide factura se emite FE y se suprime el POS), pero esa decisión vivía implícita en el flujo "cliente
pide factura" sin un punto único que la rutee por venta.

## Decisión

### D1 — El documento por venta = función de (capacidad del tenant, intención por venta)

La venta lleva una **intención de documento** opcional (`pos` | `fe` | `None`). El cierre fiscal la rutea
contra las capacidades del tenant en un único punto puro (`_resolver_documento`):

| Capacidad del tenant | Intención `None` (default) | Intención `pos` | Intención `fe` |
|---|---|---|---|
| `pos_electronico` (⇒ `facturacion_electronica`) | **POS** (FE a pedido) | POS | **FE** (suprime POS, D1) |
| `facturacion_electronica` solo (FE-only) | **FE** | FE¹ | FE |
| sin capacidad fiscal | **— (sin documento)** | —¹ | —¹ |

¹ La intención se respeta **solo si el tenant tiene la capacidad**. Si pide un documento que no puede
emitir, cae al **default por capacidad** (nunca emite lo que no puede; nunca degrada a "sin documento"
por una intención).

**Default por capacidad** (intención `None`):
- `pos_electronico` ON → **POS** (su documento natural de mostrador; FE solo a pedido).
- `facturacion_electronica` ON y `pos_electronico` OFF → **FE**.
- ninguna → **sin documento DIAN** (la venta queda solo interna: inventario y caja, sin emisión).

### D2 — Un único punto de ruteo, dos cableados (se reusa el contrato de ADR 0012 D2)

`cerrar_venta_con_pos` se generaliza a `cerrar_venta_fiscal(*, capacidades, intencion=None, …)`: resuelve
el documento y crea el pendiente correspondiente (`crear_pendiente_pos` o el nuevo `crear_pendiente_fe`),
**reusando** el split crear-pendiente / emitir-en-worker y el contrato innegociable:

- **Jamás rompe la venta:** un fallo del cierre se traga y se loguea (`cierre_fiscal_fallo`).
- **Idempotente:** clave fija por venta (`pos:{venta_id}` / `fe:{venta_id}`); en replay no re-encola
  (evita una segunda emisión y un segundo documento DIAN).
- **Commit-antes-de-encolar:** el pendiente se commitea ANTES de encolar `emitir_documento` (sin la
  carrera commit↔encolado del fix de auditoría).
- **Sin documento ⇒ sin commit:** cuando no hay capacidad fiscal el núcleo no toca la transacción de la
  venta.

El mismo ruteo se cabla en los **dos puntos de hoy**: el router HTTP `/ventas` (`encolar_cierre_pos`) y
el `CierrePos` del agente (bot). La rama FE necesita `ConfigFiscal` (reserva el consecutivo con
`config.prefix`); la rama POS no (número/prefijo los asigna MATIAS, ADR 0012 D4), así que cada cableado
**carga la config solo cuando rutea FE** (el bot no se pega al control DB para una venta POS).

### D3 — La exclusión POS↔FE se reusa, no se reimplementa (ADR 0012 D1)

La rama FE delega en `service.crear_pendiente`, que ya **suprime el POS pendiente** de la misma venta y
reserva el consecutivo. La intención `fe` sobre un tenant POS = "FE a pedido": emite FE y el POS no
resucita. La exclusión en la otra dirección (la venta ya tiene FE ⇒ el cierre POS no crea otro) también
se conserva. No hay lógica de exclusión nueva.

### D4 — FE a consumidor final permitida; identificar al cliente es opcional

La rama FE no exige datos de cliente: sin ellos se emite a **consumidor final** (`222222222222`, ya
resuelto en el payload UBL al emitir, ADR 0012 D8). Identificar al adquirente es opcional a nivel de esta
decisión (el umbral DIAN de identificación sigue siendo D8, en el flujo de venta).

### D5 — Cambio tardío = nota + nuevo documento, nunca editar

Coherente con ADR 0012 D1: una vez emitido el documento de una venta, cambiar de POS a FE (o corregir) se
hace con **nota de crédito/débito + nuevo documento**, no editando el documento emitido. El ruteo por
intención solo aplica **antes** del cierre (el hook dispara una sola vez, en venta nueva).

### D6 — "No registrar ante DIAN" es ausencia de capacidades del tenant, NUNCA una opción por venta

No existe una intención "sin documento". Que una venta no genere documento DIAN es **consecuencia** de
que el tenant no tenga capacidad fiscal, decidida a nivel de empresa (capacidades del control DB). Una
intención por venta solo elige **entre** los documentos que el tenant **sí** puede emitir. Esto evita que
una venta individual "se salte" la obligación fiscal de un tenant obligado.

## Alcance de este slice (F2.3a)

- **Sí:** `_resolver_documento` + `cerrar_venta_fiscal` + `service.crear_pendiente_fe` + el ruteo cablado
  en los dos puntos (router `/ventas` y `CierrePos`), detrás de los flags existentes, sin UX.
- **La intención se PLUMBEA** como parámetro opcional (`intencion`, default `None` → default por
  capacidad) por todo el camino (núcleo, `CierrePos`, `encolar_cierre_pos`, `CierreVentaPort`). Hoy los
  call sites no la pasan (todos cierran por default de capacidad).
- **No:** persistir la intención en la venta ni elegirla en el dashboard/bot (eso es **F2.3b/c**); ni
  cambios de UX, worker o esquema.

## Consecuencias

**A favor:** un solo punto decide el documento de toda venta (POS-default, FE on-demand, FE-only,
no-fiscal); los tenants FE-only ganan cierre automático sin tocar su flujo manual; el contrato del
cierre POS (no rompe la venta, idempotente, commit-antes-de-encolar, exclusión D1) se hereda entero; la
intención queda enchufada para que F2.3b/c solo tenga que originarla (UI/persistencia).

**En contra / costo:** la rama FE carga `ConfigFiscal` (descifra secretos del control DB) en el camino de
venta — acotado a tenants que ruteen FE y con carga perezosa (la rama POS no la paga); el nombre
histórico `CierrePos`/`encolar_cierre_pos` queda algo estrecho frente a su alcance ya general (se
conserva para no regar el cambio por wiring/Port; el núcleo sí pasa a `cerrar_venta_fiscal`).

**Pendiente (siguientes slices):** F2.3b/c — originar y persistir la intención por venta desde el
dashboard y el bot ("cliente pide factura"), y mostrar el estado fiscal de la venta.
