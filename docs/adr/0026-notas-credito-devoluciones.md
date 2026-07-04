# ADR 0026 — Notas crédito/débito y devoluciones (Fase 3 Contable B)

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0014 (documento por venta), ADR 0025 (COGS por promedio ponderado + snapshot de
  costo en la SALIDA), reglas no negociables #7 (nada mueve stock sin movimiento), #8 (idempotencia) y
  #2 (datos solo por repositorios). Cierra la deuda **FF-1** (idempotencia estricta).

## Contexto

Hasta la Fase 2 la única forma de "deshacer" una venta era el **borrado físico** (`ventas/service.py::
borrar_venta`), acotado a ventas de HOY y bloqueado cuando la venta tenía una factura electrónica viva
(`VentaConFacturaViva`). Eso deja dos huecos contables:

1. **Venta transmitida a DIAN:** una vez emitido el documento fiscal no se puede "borrar" la venta —la
   DIAN ya tiene el registro—. La corrección legal es una **nota crédito** (baja) o **nota débito**
   (alza), no un `DELETE`.
2. **Devolución de mercancía:** al aceptar una devolución, la mercancía vuelve al inventario y hay que
   reintegrar el dinero. Sin un flujo dedicado, ni el stock ni la caja/fiado ni el COGS quedaban bien.

Además, `movimientos_inventario` ya tenía el tipo de enum `DEVOLUCION` **sin uso**, y la idempotencia de
las operaciones de dinero era inconsistente (FF-1): algunas comparaban el payload al reusar una key,
otras no.

## Decisión

### D1 — Nota crédito/débito por el pipeline MATIAS existente (`modules/facturacion/notas.py`)

Archivo **nuevo y autocontenido** (`NotasService` + `SqlNotasRepository`), no se toca `service.py`/
`repository.py` de facturación (la Fase 4 los edita en paralelo). `emitir_nota_credito` /
`emitir_nota_debito`:

- Persisten la nota en `notas_electronicas` (estado `pendiente`), reusando el enum `fe_tipo`
  (`nota_credito` | `nota_debito`).
- Emiten por el cliente MATIAS inyectado (reusan `EmisionResultado`; el estado que persisten espeja la
  emisión de facturas: `pendiente → aceptada | rechazada | error`).
- Registran el desenlace en `eventos_dian` (bitácora).
- Son **idempotentes** por `idempotency_key` UNIQUE: reusar la key devuelve la nota sin re-emitir.

El **borrado físico queda SOLO para ventas no transmitidas a DIAN**: cuando la venta tiene un documento
fiscal aceptado, el guard de ventas (`tiene_factura_viva` → `VentaConFacturaViva`) ya bloquea el
`DELETE`; la corrección obligatoria es la nota crédito. La construcción del UBL fino de la nota
(referencia a la factura, códigos de motivo DIAN) se confirma contra el sandbox MATIAS en una fase
posterior; aquí el payload es mínimo y los tests usan los fakes existentes (nunca el MATIAS real).

### D2 — Devolución como orquestación transaccional (`modules/devoluciones/`)

Módulo **nuevo** (`service`/`repository`/`models`/`schemas`/`errors`). `DevolucionesService.devolver`
(total o parcial) hace, en UNA transacción:

1. **Stock:** por cada línea de catálogo devuelta, un movimiento `DEVOLUCION` (`cantidad`,
   `referencia=devolucion:{id}`, `fecha_operacion=hoy`) y restaura `inventario.stock_actual` (fila
   bloqueada con FOR UPDATE). El `costo_unitario` del movimiento es el **snapshot de la SALIDA original**
   (`movimientos_inventario.costo_unitario` de `referencia=venta:{id}`), **no** el `costo_promedio`
   actual: la mercancía re-ingresa al costo con que salió (ver D4).
2. **Dinero (contrapartida):** si la venta fue en efectivo → **egreso** de caja por el total devuelto
   (`referencia=devolucion:{id}`); si fue a crédito → **abono** al fiado de la venta (reduce la deuda,
   sin sobre-abonar si ya había pagos parciales).
3. **Nota crédito:** si la venta fue transmitida a DIAN (factura `aceptada`), emite la nota crédito (D1)
   y la liga (`devoluciones.nota_id`).

Tablas nuevas (tenant **0031**): `devoluciones` (cabecera: `venta_id`, `nota_id`, `total`,
`metodo_reintegro`, `idempotency_key` UNIQUE, `estado`) y `devoluciones_detalle` (líneas con el costo
snapshot). `notas_electronicas` se amplía con `venta_id`, `consecutivo`/`prefijo`, `idempotency_key`
UNIQUE, `dian_respuesta`, `intentos`, `emitido_en` (todo aditivo, NULL-safe).

### D3 — Invariante "nada mueve stock sin contrapartida" (regla #7)

La contrapartida de dinero se **valida antes** de tocar el stock: reintegro en efectivo exige caja
abierta (`CajaRequerida`), reintegro a crédito exige un fiado ligado (`FiadoNoEncontrado`). Si falta,
se lanza **antes** de insertar la cabecera/movimientos y la transacción entera se revierte: nunca queda
stock re-ingresado sin su egreso/abono, ni al revés (ambos van en la misma sesión del tenant).

### D4 — El COGS no se distorsiona: contra-COGS al costo snapshot

El P&L (`reportes/repository.py::estado_resultados`) ahora computa el costo de ventas como
**`Σ SALIDA(costo×cant) − Σ DEVOLUCION(costo×cant)`** (antes solo sumaba las SALIDA), con el mismo
anclaje de fecha `coalesce(fecha_operacion, creado_en)` de la 0029. Como la `DEVOLUCION` lleva el costo
del **snapshot original**, una devolución total revierte exactamente el COGS de la venta —aun si el
`costo_promedio` del producto se movió ese día por una compra a otro precio—. Sin el snapshot, revertir
al promedio del día distorsionaría tanto el valor de inventario como el COGS.

### D5 — Idempotencia estricta (cierra FF-1)

`DevolucionesService.devolver` endurece el patrón: misma `idempotency_key` + **mismo** payload
(venta + firma de líneas order-insensible, agregada por producto) → **replay** (devuelve la devolución
existente sin duplicar stock/caja/nota); misma key + payload **distinto** → `DevolucionConflicto` (409).
El ancla es la fila `devoluciones` (UNIQUE); las contrapartidas (egreso, abono, nota) cuelgan de esa
fila con keys derivadas (`devolucion-fiado:{id}`, `devolucion-nc:{id}`), así el replay no las re-ejecuta.

### D6 — Sobre-devolución bloqueada por el acumulado (no solo por request)

La idempotencia por key no basta: dos devoluciones con **keys distintas** podrían re-ingresar más stock
del vendido y reintegrar el dinero dos veces. `_resolver` acota lo devolvible con el **acumulado** de
`devoluciones_detalle` de la venta (`devuelto_por_venta`):

- **Parcial:** las cantidades del payload se AGREGAN por producto (dos líneas del mismo producto suman)
  y `pedido + ya_devuelto ≤ vendido`, o `DevolucionExcedeVenta` (409).
- **Total:** devuelve el **remanente** por línea (vendido − ya devuelto); si no queda nada →
  `NadaPorDevolver` (409). Una línea varia (sin `producto_id`, no rastreable individualmente) solo entra
  en la primera devolución de la venta.

### D7 — La devolución bloquea borrar/editar la venta (409 legible)

Nuevo guard en `ventas/service.py::_guard_modificacion` (tras el de factura viva): si la venta tiene una
devolución (`tiene_devolucion`, lectura cross-módulo en el repo) → `VentaConDevolucion` (409). La
devolución ya movió stock y dinero; borrar o reescribir la venta dejaría esas contrapartidas colgando
(sin el guard, el `DELETE` moriría en la FK `devoluciones.venta_id` con un 500 opaco).

### D8 — Superficie HTTP: `POST /devoluciones` (feature fina `ventas`)

`modules/devoluciones/router.py`, montado en `apps/api/main.py`. Idempotente por header
`Idempotency-Key` (replay → 200). La composición carga MATIAS/config del control DB si hay tenant
resuelto; sin credenciales la devolución sale igual y la nota queda `error` reintentable — **la emisión
DIAN nunca bloquea el reintegro**. Mapeo: 404 venta inexistente; 409 caja/fiado/conflicto/sobre-
devolución/nada por devolver; 422 línea no vendida o payload inválido.

## Consecuencias

- La corrección de una venta transmitida a DIAN es una nota crédito/débito trazable, no un borrado que
  contradiría el registro fiscal.
- Una devolución deja el inventario, la caja/fiado, el COGS y (si aplica) el documento DIAN consistentes
  en una sola operación idempotente. El tipo de enum `DEVOLUCION` deja de estar sin uso.
- El arqueo del día **cuadra** tras una venta + su devolución sin doble conteo: la venta suma a
  `ventas_efectivo` (tabla `ventas`) y la devolución resta el egreso (`caja_movimientos`); la venta NO
  se anula ni se excluye, así que el neto es el saldo físico real.
- Migración tenant **0031** (`0031_notas_devoluciones`), aditiva, NULL-safe y con `downgrade` limpio
  (verificado `upgrade head` + `downgrade 0030` + re-`upgrade` + `downgrade base` contra base local).
- Desviación vs. spec: el UBL fino de la nota se deja como payload mínimo (se confirma contra sandbox
  MATIAS luego), en línea con cómo se estacionaron otras integraciones DIAN; el foco de la fase fueron
  los invariantes contables (stock+contrapartida, idempotencia, COGS, arqueo, aislamiento). Los cambios
  se acotaron a archivos/módulos **nuevos** más ediciones aditivas en `reportes`, `facturacion/models`
  y el guard/mapeo en `ventas` (D7), para minimizar conflictos con la Fase 4 (retenciones).
- Pendiente (fase posterior): worker de reintentos para notas en `error` (espejo del de facturas) y el
  UBL definitivo de la nota contra el sandbox MATIAS.
