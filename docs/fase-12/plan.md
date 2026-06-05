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
| **1** | **Inventario CRUD** — crear/editar/eliminar producto (fracciones, mayorista, escalonado, stock inicial) | repo+service+router en `modules/inventario` | completar `TabInventario` (solo-lectura → CRUD admin) | ✅ hecho |
| **2** | **Reportes pesados** — Resultados (P&L, costo exacto opción C), Top productos | costo al vender + `modules/reportes` (consultas agregadas) | tabs Resultados / Top productos | ✅ hecho |
| **3** | **Facturación** — historial + emitir | `modules/facturacion` (listar/detalle; emit ya existía) | tab Facturación (gateado) | ✅ hecho |
| **4a** | **Compras** — registrar compras a proveedor (suman stock, fijan costo) | `modules/compras` (núcleo) | tab Compras | ✅ hecho |
| **4b** | **Proveedores / cuentas por pagar** (deuda, abonos, saldo) + fotos Cloudinary | `modules/proveedores` (núcleo) | tab Proveedores | **🚧 en progreso** |
| 5 | Libro IVA | `modules/facturacion` (libro/consolidado) | tab Libro IVA | ⏳ pendiente |
| **6a** | **Compras fiscal (DATOS)** — registrar compras fiscales con desglose de IVA (alimenta el Libro IVA) | `modules/compras_fiscal` (núcleo de datos, gateado por `compras_fiscal`) | tab Compras Fiscal | **🚧 en progreso** |
| 6b | **RADIAN-FE recibidas** (eventos 030-033, acuse) + notas, DS-NO, honorarios | `modules/facturacion` / `compras_fiscal` (DIAN inbound) | tabs de la cola fiscal | ⏸️ DIFERIDO (contrato MATIAS sin confirmar) |

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

---

## Slice 2 — Reportes pesados: Resultados + Top productos (en progreso)

**Costo de ventas exacto (opción C):** el costo se hila a la venta. Al vender una línea de catálogo, el
movimiento **SALIDA** guarda `costo_unitario = producto.precio_compra` **al momento de vender**; las líneas
varia (sin `producto_id`) no generan movimiento ni costo. Las ventas anteriores a este cambio quedan con
`costo_unitario` NULL → cuentan como 0 en el P&L (etiquetado en la UI).

### Backend (`modules/ventas` + `modules/reportes`)

- **Parte A — costo al vender:** `ProductoPrecio`/`LineaResuelta` llevan el costo; `crear_venta` lo escribe
  en el SALIDA. No cambia el shape de `VentaLeer` ni rompe las pruebas de venta.
- **`GET /reportes/resultados`** (admin, sin scoping): P&L de un rango (`?desde&hasta`, default mes). ingresos
  = Σ subtotal de ventas no anuladas (sin IVA); costo_ventas = Σ(costo×cantidad) de SALIDA (NULL=0);
  utilidad_bruta = ingresos − costo_ventas; gastos = Σ gastos; utilidad_neta = bruta − gastos.
- **`GET /reportes/top-productos`** (vendedor; scope por `get_filtro_efectivo`): ranking por ingreso del
  rango (`?desde&hasta&limite`, default mes), `GROUP BY` producto sobre `ventas_detalle` de ventas no
  anuladas, excluye varia.

### Frontend

- `TabResultados` (solo admin; oculto y sin pedir el endpoint para vendedor) — tarjetas del P&L + gráfica
  (recharts) + selector de rango (default mes) + etiqueta del costo exacto.
- `TabTopProductos` (vendedor/admin) — tabla del ranking + gráfica + selector de rango. Ruta `/top-productos`.

### Tests

- **pytest:** una venta de catálogo deja el SALIDA con `costo_unitario = precio_compra`; una varia no genera
  costo; resultados cuadran y excluyen anuladas; admin-only (vendedor → 403); top-productos ordenado por
  ingreso desc, respeta scoping, excluye anuladas/varia.
- **Vitest:** cada tab pide su endpoint y pinta los números; Resultados no visible/no pide para vendedor.

---

## Slice 3 — Facturación: historial + emitir (en progreso)

La **emisión asíncrona ya existía** (Fase 6): `POST /facturas {venta_id}` → crea `pendiente` → el worker
emite por MATIAS → estado por SSE. Este slice añade el **historial** y el **tab**, gateados por
`facturacion_electronica`.

### Backend (`modules/facturacion`)

- `SqlFacturacionRepository.listar(desde?, hasta?, estado?) -> list[FacturaLeer]` (rango hora Colombia,
  más reciente primero). `FacturaLeer` gana `creado_en` (fecha).
- `SqlFacturacionRepository.detalle(id) -> FacturaDetalle | None`: base + `emitido_en` + `total` (de la
  venta ligada) + `motivo` (extraído de `dian_respuesta`: `rechazo`/`error`).
- Router gateado (`require_feature("facturacion_electronica")`, `require_role("vendedor")`):
  `GET /facturas` (?desde&hasta&estado) y `GET /facturas/{id}` (404 si no existe). **`POST /facturas` no se
  tocó** (es el emit).

### Frontend (`TabFacturacion`, ruta `/facturacion` ya gateada por `RUTA_FEATURE`)

- Historial: lista (prefijo-consecutivo, fecha, estado con badge, cufe); estado en vivo por SSE
  (`factura_pendiente/aceptada/rechazada/error`, `reconnected`) → re-fetch. Al expandir → detalle con
  total y **motivo de rechazo** si aplica.
- Emitir: sobre ventas recientes NO facturadas, con **confirmación fuerte** ("…factura electrónica REAL
  ante la DIAN… legal e IRREVERSIBLE…"); solo al confirmar → `POST /facturas {venta_id}` (+ Idempotency-Key).
  Cancelar no postea.

### Tests

- **pytest:** `listar` ordena/filtra por estado; `detalle` trae total + motivo en una rechazada; sin
  feature → 404; shapes del router.
- **Vitest:** lista + badges; emitir abre confirmación y solo al confirmar postea con shape +
  Idempotency-Key; cancelar no postea; detalle trae el motivo; la ruta no aparece sin la feature.

---

## Slice 4a — Compras (en progreso)

**Compras es NÚCLEO** (`'compras'` no está en `OPCIONALES` de `core/tenancy/catalogo.py`) → sin
`require_feature`. Lo fiscal (`compras_fiscal`/RADIAN) sí va gateado y se movió al **Slice 6**. El CRUD de
proveedores + cuentas por pagar (Cloudinary) es el **Slice 4b**. Las tablas ya existían (tenant 0001).

### Backend (`modules/compras`, RBAC = admin)

- Modelos `Proveedor`, `Compra`, `CompraDetalle` (sin `empresa_id`).
- `POST /compras { proveedor:{id?}|{nombre,nit?}, fecha?, items:[{producto_id,cantidad,costo}] }`:
  get-or-create del proveedor; inserta compra + detalle; por item genera **ENTRADA** (`costo_unitario=costo`,
  `referencia "compra:{id}"`) que **suma** stock (regla #7) y fija `productos.precio_compra` al costo de esa
  compra (el costo grabado en ventas pasadas NO se toca). `total = Σ(cantidad×costo)` en el servidor. `201`.
  Emite `compra_registrada` + `inventario_actualizado`.
- `GET /compras (?desde&hasta, default mes, hora Colombia)` → lista con proveedor + total. PUT/DELETE
  diferidos (editar una compra que ya movió stock requiere reversa → otro slice).

### Frontend (`TabCompras`, reemplaza el stub; admin-only como Resultados)

Registrar compra (proveedor por nombre/nit + items: buscar producto vía `/productos?q`, cantidad, costo) y
lista del rango. Vendedor: tab bloqueado ("solo administradores"). Live: re-fetch ante
`compra_registrada` / `inventario_actualizado`.

### Tests

- **pytest** (integración Postgres): compra crea compra+detalle, ENTRADA por item (stock sube),
  `productos.precio_compra` queda en el costo, total correcto, get-or-create dedup, admin-only (403),
  listado por rango, emite el evento.
- **Vitest:** admin registra (POST /compras con shape correcto) y ve la lista; vendedor sin controles.

---

## Slice 4b — Proveedores / cuentas por pagar + fotos Cloudinary (en progreso)

**Proveedores es NÚCLEO** (sin `require_feature`); RBAC = admin. Tablas ya existían (tenant 0001:
`facturas_proveedores` con id TEXT = nº de factura del proveedor, `facturas_abonos`).

### Parte A — Cloudinary como secreto por-empresa (provisioning)

- Onboarding JSON: bloque `cloudinary: {cloud_name, api_key, api_secret}`. `cargar_secretos_empresa`
  guarda `api_key`/`api_secret` **cifrados** en `secretos_empresa` (claves `cloudinary_api_key`/
  `cloudinary_api_secret`) y `cloud_name` en claro en `config_empresa`. Claves ausentes → se omiten.
- `cargar_config_cloudinary(session, master, empresa_id) -> CloudinaryCredenciales | None` (espeja
  `cargar_config_matias`): descifra; None si la empresa no tiene Cloudinary. `pyproject`: +cloudinary,
  +python-multipart.

### Parte B — Cuentas por pagar (`modules/proveedores`, RBAC admin)

- `POST /proveedores/facturas {id,proveedor,descripcion?,total,fecha?}` → pagado=0, pendiente=total,
  estado='pendiente'. id duplicado → 409.
- `POST /proveedores/abonos {factura_id,monto,fecha?}` → inserta abono; recalcula pagado=Σabonos,
  pendiente=total−pagado (clamp 0), estado='pagada' si pendiente≤0. 404 si la factura no existe; 422 si
  monto≤0 o excede el pendiente (criterio: no sobre-abonar).
- `GET /proveedores/facturas (?estado)` → lista con saldo. `GET /proveedores/resumen` → total adeudado.

### Parte C — Foto a Cloudinary (gateada a "configurado")

- `CloudinaryClient` perezoso (no importa el SDK ni toca red al construir; `upload` en `asyncio.to_thread`,
  `resource_type="auto"`). `POST /proveedores/facturas/{id}/foto` (multipart): **503** si la empresa no
  tiene Cloudinary; si sí, sube y guarda `foto_url`/`foto_nombre`.

### Parte D — Frontend (`TabProveedores`, admin-only, reemplaza el stub)

Resumen del total adeudado + lista de cuentas por pagar; registrar factura y abono (recalcula y muestra
el saldo); subir foto **solo si Cloudinary disponible** (ante 503 oculta el control con aviso). Vendedor:
bloqueado. Live: re-fetch ante `reconnected`.

### Tests

- **pytest:** provisioning carga/recupera Cloudinary cifrado (None si ausente); factura nace pendiente;
  abono recalcula; abonos que saldan → 'pagada'; dedup 409; 404/422; resumen suma; admin-only 403; foto
  con fake → guarda URL; sin Cloudinary → 503 (nunca red real).
- **Vitest:** registrar factura/abono postea el shape correcto y el saldo se actualiza; el control de foto
  se comporta según disponibilidad (503 → oculto + aviso); vendedor sin acceso.

---

## Slice 6 — Cola fiscal (re-troceado)

El Slice 6 original (toda la cola fiscal en un bloque) se parte: **6a = solo DATOS** de compras fiscales
—lo que el Libro IVA necesita ya— y **6b = RADIAN-FE recibidas** (eventos 030-033, acuse), que **se
difiere** porque el contrato de MATIAS para FE recibidas aún no está confirmado. La tabla `compras_fiscal`
(tenant 0001) trae ambas mitades: columnas de datos (`base`/`iva`/`total`/`soporte_url`) y columnas RADIAN
(`cufe_proveedor`, `evento_030_at`…`evento_033_at`, `evento_estado`, `evento_error`). El 6a llena las
primeras y **deja NULL** las RADIAN.

### Slice 6a — Compras fiscal (DATOS, sin RADIAN) (en progreso)

**Compras fiscal es OPCIONAL** (`'compras_fiscal'` ∈ `OPCIONALES`) → router gateado con
`require_feature("compras_fiscal")` (404 sin la capacidad). RBAC = admin. La tabla ya existía. **No toca
RADIAN/DIAN ni MATIAS:** solo persiste el desglose de IVA que alimenta el Libro IVA (Slice 5).

#### Backend (`modules/compras_fiscal`, RBAC = admin)

- Modelo `CompraFiscal` que mapea **solo** las columnas de datos; las columnas RADIAN quedan sin mapear
  (NULL), reservadas al Slice 6b.
- `POST /compras-fiscal { proveedor_nit, base, iva, total, soporte_url?, compra_id? }` → inserta. Valida
  montos `>= 0` y coherencia `base + iva == total` tolerando ±1 centavo (redondeo por línea de la DIAN);
  incoherencia → `422`. `201`.
- `GET /compras-fiscal (?desde&hasta, default mes, hora Colombia por `creado_en`)` → lista, recientes primero.
- `POST /compras/{id}/to-fiscal` → deriva una entrada fiscal de una compra normal (toma su total; base/iva
  en 0: el desglose no se conoce). **Idempotente** por `compra_id` (si ya hay fiscal ligada, devuelve la
  existente con `200`; si la crea, `201`). `404` si la compra no existe.
- (`to-compras` / `bulk` DIFERIDOS — no aportan al Libro IVA ahora.)

#### Frontend (`TabComprasFiscal`, ruta `/compras-fiscal` ya gateada por `RUTA_FEATURE`)

Admin: registrar compra fiscal (nit, base, iva, total, soporte) + lista del rango; botón **"marcar fiscal"**
sobre una compra normal (`to-fiscal`), con badge "fiscal" en las ya derivadas. Vendedor: bloqueado. Live:
re-fetch ante `reconnected` / `compra_registrada`.

#### Tests

- **pytest** (integración Postgres): registrar fiscal persiste el desglose; lista por rango; to-fiscal crea
  (total de la compra, base/iva 0) y es idempotente (no duplica); compra inexistente → 404; gate sin la
  feature → 404; admin-only → 403; montos incoherentes / negativos → 422.
- **Vitest:** registrar postea el shape correcto; pinta la lista; "marcar fiscal" postea a `to-fiscal`;
  vendedor sin controles; la ruta no aparece sin la feature.

### Slice 6b — RADIAN-FE recibidas + cola fiscal restante (DIFERIDO)

FE recibidas (eventos RADIAN 030-033, acuse de recibo), notas electrónicas, DS-NO y honorarios. **Bloqueado**
hasta confirmar el contrato de MATIAS para FE recibidas; llenará las columnas RADIAN de `compras_fiscal` y
el resto de la cola fiscal inbound.
</content>
</invoke>
