# Fase 12 — Amplitud (tabs fiscales + CRUD + reportes pesados) · plan + troceo

> La Fase 11 entregó el **dashboard núcleo** (shell + tabs operativos) y difirió a esta fase lo que
> requería **backend nuevo**: CRUD de catálogo, reportes pesados y toda la cola fiscal. Fase 12 cierra
> esos huecos por **slices** verticales (backend + frontend + tests por slice), no por capas.

## Estrategia

- **Vertical por slice:** cada slice entrega backend (TDD pytest) **y** su frontend (Vitest) juntos, de
  modo que cada uno deja una capacidad usable de punta a punta.
- **Reusar antes de escribir:** repos/servicios/eventos ya existen (inventario, ventas, facturación);
  el trabajo es completar lo que la Fase 11 dejó solo-lectura o sin endpoint.
- **Reglas no negociables intactas:** aislamiento por tenant, SQL solo en repos, `async/await`,
  zona horaria Colombia, nada toca stock sin movimiento (regla #7), eventos por `publish()`.

## Troceo

| Slice | Alcance | Backend | Frontend | Estado |
|---|---|---|---|---|
| **1** | **Inventario CRUD** — crear/editar/eliminar producto (fracciones, mayorista, escalonado, stock inicial) | repo+service+router en `modules/inventario` | completar `TabInventario` (solo-lectura → CRUD admin) | **🚧 en progreso** |
| 2 | Reportes pesados — Resultados (P&L), Top productos | `modules/reportes` (consultas agregadas) | tabs Resultados / Top productos | ⏳ pendiente |
| 3 | Facturación — historial + tab | `modules/facturacion` (listado/detalle) | tab Facturación | ⏳ pendiente |
| 4 | Compras / Compras fiscal / Proveedores | `modules/compras` (+ fiscal), `modules/proveedores` | tabs Compras, Compras fiscal, Proveedores | ⏳ pendiente |
| 5 | Libro IVA | `modules/facturacion` (libro/consolidado) | tab Libro IVA | ⏳ pendiente |
| 6 | Cola fiscal — FE recibidas, notas, DS-NO, RADIAN, honorarios | `modules/facturacion` (DIAN inbound + documentos soporte) | tabs de la cola fiscal | ⏳ pendiente |

---

## Slice 1 — Inventario CRUD (en progreso)

**Punto de partida:** hoy el inventario es **solo lectura + ajuste de stock**. Faltan crear/editar/eliminar
productos con sus fracciones y precios (mayorista y escalonado por umbral).

### Backend (`modules/inventario`, RBAC = admin)

- **Schemas** `ProductoCrear` / `ProductoActualizar` (+ `FraccionCrear`): nombre, codigo?, categoria?,
  marca?, unidad_medida, precios (venta/compra?/mayorista?/escalonado: umbral+bajo+sobre), iva (0..100),
  permite_fraccion, activo, fracciones[], stock_minimo; `ProductoCrear` añade stock_inicial?. Validación:
  montos `>= 0`, iva `0..100`.
- **`POST /productos`** → crea `Producto` + su fila `Inventario` (stock_actual=0). Si `stock_inicial > 0`,
  registra un movimiento **ENTRADA** (regla #7) y deja el stock. Crea las fracciones. `201`. `codigo`
  duplicado → `409`.
- **`PUT /productos/{id}`** → actualiza campos + **reemplaza** fracciones (cascade delete-orphan). **No**
  toca `stock_actual` (eso va por `/inventario/ajuste`); sí actualiza `stock_minimo`. `200/404`.
- **`DELETE /productos/{id}`** → **soft delete** (`activo=false`): los productos están referenciados por
  ventas, nunca se borran en duro. `200/404`. Un inactivo no sale en el listado por defecto (filtro
  `activo` existente).
- Cada mutación emite SSE `inventario_actualizado` por `publish()`.

### Frontend (`TabInventario`)

- Solo **admin** ve los controles (nuevo / editar / eliminar); el vendedor sigue en solo-lectura.
- Form con datos + precios mayorista/escalonado + fracciones + iva + permite_fraccion + stock mínimo/inicial,
  cableado a `POST`/`PUT`/`DELETE /productos` por `api.js`. Confirmación al eliminar.
- Live: re-fetch ante `inventario_actualizado`.

### Tests

- **pytest** (integración contra Postgres efímero): crear (con/sin stock_inicial → verifica la ENTRADA),
  editar (reemplaza fracciones), soft-delete (activo=false, no aparece en lista activa), admin-only
  (vendedor → 403), emisión del evento, validación de montos.
- **Vitest:** admin ve los controles y el create postea el shape `ProductoCrear` correcto; vendedor NO ve
  controles; editar hace `PUT`; eliminar hace `DELETE` con confirmación.
</content>
</invoke>
