# ADR 0025 — COGS por promedio ponderado móvil + datos contables (Fase 2 Contable A)

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0014 (documento por venta), ADR 0022 (cobro de cita → venta), regla no
  negociable #7 (nada mueve stock sin movimiento) y #8 (idempotencia)

## Contexto

El costo de ventas (COGS) del P&L (`modules/reportes`) hilaba el **último** `productos.precio_compra`
al movimiento `SALIDA` al vender (opción C histórica). Con precios de compra volátiles esto sesga la
utilidad: vender inventario comprado barato con el último costo (caro) subestima el margen, y
viceversa. El estándar contable colombiano para inventarios es el **promedio ponderado**.

Además, dos desalineaciones de datos estorbaban la profesionalización contable:

1. El P&L mezclaba **fechas**: ingresos por `Venta.fecha` y costo por `MovimientoInventario.creado_en`.
   Al **editar** una venta de hoy (`aplicar_edicion`), sus `SALIDA` se re-crean con un `creado_en`
   nuevo mientras la venta conserva su fecha → ingreso y costo podían caer en días distintos.
2. Siete tablas existían en el esquema (migración 0001) **sin modelo ORM** — inaccesibles por la capa
   de repositorios (regla #2): `notas_electronicas`, `documentos_soporte`, `eventos_dian`,
   `iva_saldos_bimestrales`, `libro_iva`, `cuentas_cobro`, `bancolombia_transferencias`.

## Decisión

### D1 — `productos.costo_promedio`: promedio ponderado móvil, recalculado en cada COMPRA

Columna nueva `productos.costo_promedio NUMERIC(12,2)` (migración tenant **0028**), sembrada en el
backfill desde el último `precio_compra` (los movimientos históricos NO se tocan). Cada compra
recalcula (en `modules/compras/repository.py::crear_compra`):

```
nuevo_promedio = (stock_prev·promedio_actual + cantidad·costo_unitario) / (stock_prev + cantidad)
```

cuantizado a centavos con `core.money.cuantizar`. Reglas de la función pura `_promedio_ponderado`:

- `promedio_actual` NULL (producto sin costo previo) → arranca en el costo de esta compra.
- `stock_prev` negativo (modo permisivo de stock) cuenta como 0: un inventario en rojo no aporta
  valor promediable; el promedio se rehace desde el costo nuevo.
- Denominador no positivo (p. ej. cantidad 0) → cae al costo de la compra (sin división por cero).

`precio_compra` (último costo) se conserva como estaba: sigue siendo el fallback y el dato de
"último costo" para otras vistas.

### D2 — Concurrencia: `SELECT … FOR UPDATE` de la fila del producto

El recálculo es leer-modificar-escribir sobre `costo_promedio` → expuesto a *lost update* si dos
compras del mismo producto corren en paralelo. Se toma `SELECT costo_promedio FROM productos WHERE
id=:p FOR UPDATE` **antes** de leer el stock y escribir el promedio, dentro de la transacción de la
compra. El orden de locks es siempre **productos → inventario** (la venta solo bloquea `inventario`,
sin ordenamiento inverso), así que no hay riesgo de deadlock. Test de invariante crítico:
`test_compras_concurrentes_no_pierden_actualizacion_del_promedio` (dos compras concurrentes → promedio
ponderado correcto y stock sumado, no la última escritura).

### D3 — Las SALIDA snapshotean `costo_promedio` (fallback `precio_compra`)

`modules/ventas/service.py::_linea_catalogo` toma el costo del snapshot así:
`costo = costo_promedio if costo_promedio is not None else precio_compra`. Se hila hasta
`movimientos_inventario.costo_unitario` como antes. **El P&L no cambia de fórmula**: sigue sumando
los snapshots de los movimientos (`Σ costo_unitario·cantidad` de las `SALIDA`). El invariante #7
queda intacto: la `SALIDA` se sigue creando; solo cambia el VALOR del snapshot.

### D4 — Fecha del COGS: columna `fecha_operacion` (no join por tag), P&L por `coalesce`

Se elige **columna `fecha_operacion`** en `movimientos_inventario` (migración **0029**) sobre el join
`movimiento → venta`. Razón: la única "referencia" disponible es el tag de texto libre
`referencia = 'venta:{id}'` (no es una FK), así que el join exigiría parsear/castear string —frágil y
no idiomático— y solo cubriría las `SALIDA` de venta. `fecha_operacion` generaliza: snapshotea la
fecha del **documento de negocio origen** (la fecha de la venta para `SALIDA`, la de la compra para
`ENTRADA`; NULL para ajustes). El P&L filtra el COGS por
`coalesce(fecha_operacion, creado_en)`, anclándolo a la fecha de la venta. Backfill NULL-safe:
`fecha_operacion = creado_en` por defecto y, para las `SALIDA` con tag de venta, la fecha de esa venta
(el tag se usa una sola vez, en la migración, sin dejar un join permanente). Tests:
`test_cogs_cuenta_por_fecha_operacion_no_por_creado_en` y el fallback a `creado_en`.

### D5 — ORM de las 7 tablas huérfanas + migración de reconciliación

Se crean los modelos SQLAlchemy espejando el DDL real de la 0001, en sus módulos naturales:

| Tabla | Modelo | Módulo |
|---|---|---|
| `notas_electronicas` | `NotaElectronica` | `modules/facturacion/models.py` |
| `documentos_soporte` | `DocumentoSoporte` | `modules/facturacion/models.py` |
| `eventos_dian` | `EventoDian` | `modules/facturacion/models.py` |
| `iva_saldos_bimestrales` | `IvaSaldoBimestral` | `modules/reportes/models.py` (nuevo) |
| `libro_iva` | `LibroIVA` | `modules/reportes/models.py` (nuevo) |
| `cuentas_cobro` | `CuentaCobro` | `modules/cobranza/models.py` |
| `bancolombia_transferencias` | `BancolombiaTransferencia` | `modules/bancos/models.py` (nuevo) |

Las FKs entre tablas (p. ej. `documentos_soporte.cuenta_cobro_id`, `notas_electronicas.factura_id`)
se mapean como **columnas planas `BigInteger` sin `ForeignKey` en el ORM** — la FK vive en la base
(0001). Es el mismo criterio ya usado en `FacturaElectronica.venta_id`: no acoplar el grafo de
mappers entre módulos.

Migración de reconciliación tenant **0030** (`0030_orm_huerfanas`): `CREATE TABLE IF NOT EXISTS`
espejando la 0001 para las 7 tablas, garantizando que DB y metadata ORM queden alineadas en cualquier
entorno. En una base ya migrada es un **no-op total**. Su `downgrade` es intencionalmente vacío: las
tablas pertenecen a la 0001, no a esta migración; revertir la 0030 no debe borrarlas.

## Consecuencias

- La utilidad bruta refleja el costo promedio real del inventario disponible, no el vaivén del último
  costo. Un P&L más fiel para negocios con compras a precios variables.
- El COGS y el ingreso de una venta caen en el **mismo día** (la fecha de la venta), aun si la venta
  se editó más tarde.
- Los 7 planos contables (notas/documento soporte/eventos DIAN, Libro IVA, saldos bimestrales, cuentas
  de cobro, transferencias Bancolombia) ya son accesibles por repositorios (regla #2), habilitando
  futuras materializaciones (Libro IVA persistente, conciliación bancaria).
- Migraciones tenant **0028–0030**, aditivas, NULL-safe y con `downgrade` limpio (verificado
  `upgrade head` + `downgrade base` contra una base tenant local).
- Desviación vs. spec: se prefirió `fecha_operacion` al join por `referencia` (justificado en D4). El
  nombre y forma de las 7 tablas coincidió con el DDL real (0001); sin otras diferencias.
