# ADR 0027 — Retenciones, INC y libros fiscales (Fase 4 Contable C)

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0025 (COGS + ORM de `iva_saldos_bimestrales`/`libro_iva`), ADR 0012 (POS
  electrónico), reglas no negociables #2 (datos solo por repositorios), #4 (TZ Colombia), #7/#8
  (movimientos e idempotencia), multi-tenancy (DB por empresa).

## Contexto

Tras Contable A (ADR 0025) el sistema calcula el COGS por promedio ponderado y ya mapea por ORM las
tablas `iva_saldos_bimestrales` y `libro_iva`, pero **el Libro IVA se calculaba al vuelo** en
`modules/reportes/repository.py::libro_iva` (cruce de `ventas.impuestos` vs `compras_fiscal.iva` sin
persistir nada) y **no existía soporte para retenciones ni INC**. Un negocio colombiano formal necesita:

- **Retenciones** que practica o le practican: retefuente por concepto (con base mínima en UVT y tarifa),
  ICA por municipio (tarifa por mil) y reteIVA (% sobre el IVA).
- **INC** (impuesto nacional al consumo) por tipo de bien/servicio.
- **Libros** contables (auxiliar y mayor) y un **saldo de IVA bimestral** persistido, no efímero.

Restricción dura de multi-tenancy y de negocio: **nada hardcodeado** (las tarifas cambian por empresa y
por año), y **opt-in** — si el tenant no configura nada, ningún total cobrado ni la caja cambian.

## Decisión

### D1 — `config_retenciones`: una tabla editable por tenant, sin tarifas en código (migración 0032)

Una sola tabla gobierna TODO el catálogo tributario del negocio. Filas tipadas por `tipo`
(`retefuente` | `ica` | `reteiva` | `inc` | `uvt`), con clave natural `(tipo, concepto)`:

- `concepto`: el concepto de retefuente (`compras`/`servicios`/`honorarios`…), el municipio de ICA, el
  tipo de bien/servicio de INC, o el **año** para la fila `uvt`.
- `base_minima_uvt`: umbral en UVT bajo el cual no se retiene (retefuente). 0 = sin mínimo.
- `tarifa`: porcentaje (retefuente/reteiva/inc) o **por mil** (ICA); en la fila `uvt` es el **valor del
  UVT en pesos** de ese año. Guardar el UVT como una fila más evita acoplar un valor de gobierno al
  código y lo deja editable.
- `activo` / `editable`: la semilla nace **vacía** (opt-in). Cada fila es editable por la empresa.

Aislamiento: es una tabla de negocio **sin `empresa_id`** — la base ES la frontera del tenant.

### D2 — Motor PURO + persistencia idempotente en `retenciones_documento` (migración 0033)

`modules/retenciones/motor.py` es una función pura `calcular_retenciones(reglas, base_gravable, iva,
uvt_valor)`:

- **retefuente**: base = base gravable; retiene solo si `base ≥ base_minima_uvt × uvt_valor`;
  valor = base × tarifa%.
- **ica**: valor = base gravable × tarifa **‰** (por mil).
- **reteiva**: base = el **IVA** del documento; valor = IVA × tarifa%.
- **inc**: valor = base gravable × tarifa% (registrado como tributo; ver D5).

Dinero con `core.money.cuantizar` (NUMERIC(12,2), ROUND_HALF_UP). El servicio persiste los renglones en
`retenciones_documento` con clave natural `(doc_tipo, doc_id, tipo, concepto)` vía **UPSERT** (ON
CONFLICT): reaplicar el motor sobre el mismo documento **actualiza en el lugar, no duplica**.

**Invariante crítico (total cobrado/caja):** el motor **jamás** muta `ventas.total`/`subtotal`/`impuestos`
ni el total de la compra. La retención se refleja como **menor pago recibido**, no menor venta:
`neto_a_recibir = total_documento − total_retenido` (retefuente + ica + reteiva; el INC se informa
aparte). Test-primero: `test_con_retenciones_total_venta_intacto_y_caja_cuadra` (la venta queda intacta
en la tabla y el neto cuadra) y su regresión `test_sin_config_no_cambia_nada` (sin reglas, cero
renglones, `neto == total`).

Integración **aditiva y opt-in**: el router `/retenciones/*` (admin, feature `retenciones`) aplica el
motor a una venta o compra bajo demanda. Se evitó cablearlo dentro de la transacción de
`ventas`/`compras` para no chocar con la Fase 3 (notas crédito/devoluciones) que toca esos módulos en
paralelo; el punto de enganche natural queda documentado para el orquestador.

### D3 — Consolidación de IVA idempotente por bimestre (migración 0034)

`modules/reportes/consolidacion.py` deja de calcular el Libro IVA al vuelo y lo **materializa** por
período (los seis bimestres del año colombiano, aritmética pura `rango_bimestre`):

- `libro_iva`: un renglón por documento, `referencia = 'venta:{id}'` / `'compra_fiscal:{id}'`, UPSERT
  vía **índice único parcial** `uq_libro_iva_referencia` (migración 0034, `WHERE referencia IS NOT
  NULL` — los renglones históricos con `referencia` NULL quedan intactos).
- `iva_saldos_bimestrales`: un saldo por `(anio, bimestre)`, UPSERT vía la constraint de la 0001.

**Invariante crítico (idempotencia):** reprocesar el mismo período no duplica renglones ni saldos.
Test-primero: `test_reprocesar_es_idempotente_no_duplica` (tres corridas → mismo conteo y saldo). El
saldo se computa de los insumos (ventas completadas / compras fiscales), no re-sumando `libro_iva`, para
no arrastrar renglones de otra corrida. El reporte al vuelo `/reportes/libro-iva` se conserva como vista
ad-hoc de un rango arbitrario; la **fuente de verdad persistida** es la consolidación bimestral
(`/reportes/iva/consolidar`, `/reportes/iva-saldos`).

### D4 — Libros auxiliar y mayor derivados (sin PUC formal todavía)

`modules/reportes/libros.py`: el **Mayor** totaliza cada concepto del período (ingresos, IVA
generado/descontable, costo de ventas, gastos, compras y las retenciones/INC por tipo); el **Auxiliar**
lista el detalle documento a documento detrás de cada concepto, filtrable. Sin PUC formal —eso es F8—
las "cuentas" son conceptos coarse con una `naturaleza` provisional (ingreso/egreso/impuesto/retencion).
El costo de ventas se ancla a `fecha_operacion` (ADR 0025), igual que el P&L. Endpoints admin gateados
por la nueva feature `libros_contables`.

### D5 — INC: registrado, no aún incorporado al total cobrado

El INC **aumenta** lo que paga el cliente (a diferencia de una retención). En v1 se **calcula y registra**
en `retenciones_documento` (para libros/reportes) pero **no se suma al total de la venta**, por la misma
razón de D2: incorporarlo tocaría el flujo de venta que la Fase 3 modifica en paralelo. Queda como
**opt-in futuro** explícito. `total_inc` se expone aparte del neto.

## Veredicto — documento equivalente POS electrónico 2026 (con MATIAS)

**Cubierto, sin gap.** La obligación del documento equivalente **POS electrónico** (obligatorio para
pequeños desde jun-2025, calendario DIAN) está soportada **de punta a punta**:

- Tipo `pos` en `fe_tipo` (migración 0015), cierre fiscal por capacidad del tenant (ADR 0014,
  `modules/facturacion/pos_hook.py`), config POS propia (resolución/prefijo/`software_manufacturer`).
- Emisión real vía MATIAS: `MatiasClient.emitir_pos` → `POST /auto-increment/pos-documents`; MATIAS
  asigna número/prefijo y devuelve **CUDE síncrono** (parseo `_parsear_emision_pos`). Ya con switch-on en
  producción para Punto Rojo (`docs/catalogo-de-oferta.md`: "✅ Disponible").

Limitaciones menores (NO gaps de cobertura, fuera del alcance de esta fase): la **intención POS/FE por
venta** está plumbeada pero no persistida/seleccionable en UI (fase posterior), y la conversión
**pedido → POS** es v2 (ADR 0016). No se construyó nómina ni nada fuera de alcance.

## Consecuencias

- Catálogo tributario **editable por empresa** sin tarifas en código; semilla vacía = opt-in real (sin
  config, cero cambio en totales — verificado por test de regresión).
- Retenciones/INC persistidas por documento de forma **idempotente**; el total cobrado y la caja cuadran
  (retención = menor pago recibido, no menor venta).
- Libro IVA y saldo bimestral **materializados** e idempotentes por período; libros auxiliar y mayor
  disponibles como reportes derivados.
- Migraciones tenant **0032–0034**, aditivas, con `downgrade` limpio (verificado `upgrade head` +
  `downgrade base` contra una base tenant local).
- Aislamiento multi-tenant de config y saldos cubierto por tests (empresa A nunca ve datos de B).
- Nuevas features `retenciones` y `libros_contables` (opcionales, sin dependencias duras).

## Seguimiento — cableado inline + INC al total (post Fase 3/8)

Con la Fase 3 (notas/devoluciones) y la Fase 8 (motor contable, ADR 0030) ya mezcladas, se cierra el
enganche que D2 dejó "documentado para el orquestador".

### S1 — Aplicación automática en venta/compra (inline, atómica, opt-in)

`VentaService`/`ComprasService` reciben un aplicador de retenciones **opcional** (puerto estructural,
como `fiados`). Al registrar el documento, si está inyectado, se calculan y persisten sus renglones en
la **misma transacción** (`guardar_renglones(commit=False)`): commit atómico con la venta/compra —igual
que el cargo de fiado—, así un rollback del documento revierte también sus retenciones. Los routers
`POST /ventas` y `POST /compras` inyectan el aplicador **solo si el tenant tiene la feature
`retenciones`** (si no, el motor no corre ni una consulta → coste cero para quien no lo activó). El
endpoint on-demand `/retenciones/*/aplicar` sigue existiendo (`commit=True`) para recálculo/backfill
manual. Invariante intacto: el motor **jamás** muta `ventas.total`. Idempotencia: reintentar una venta
por `idempotency_key` es replay (no re-aplica) y, aun re-aplicando, el UPSERT no duplica (test).

**Alcance:** los canales cableados son los flujos HTTP canónicos (dashboard POS + compras). El bot
(`ai/tools.py`) y el cobro de cita (`agenda`) no se cablearon inline: un negocio formal que configura
retenciones opera por el dashboard, y esos canales quedan cubiertos por el `/aplicar` on-demand y el
backfill contable (ambos idempotentes). Queda como extensión futura si se requiere.

### S2 — INC opcional al total del documento (cierra el opt-in de D5)

D5 dejó el INC **registrado pero no sumado** al total. Ahora es opt-in por tenant **sin migración**:
una fila de CONFIG en `config_retenciones` con `tipo='inc_al_total'` (clave natural `(inc_al_total,
global)`), cuyo `activo` es el interruptor. `tipo` es TEXT libre (sin CHECK), así que la fila cabe en la
tabla existente; se excluye del motor junto con `uvt` (`TIPOS_CONFIG`), como valor de config, no regla.
Con el interruptor activo el resumen expone `total_con_inc = total_documento + total_inc` y
`neto_a_recibir = total_con_inc − total_retenido`; apagado, el INC se informa aparte como antes. **La
tabla `ventas` sigue intacta** (`total_documento` es siempre el de la tabla): `total_con_inc` es la
representación fiscal del documento, no una mutación del cobro. Cobrar el INC en el punto de venta
(mutar el total recaudado) exigiría meterlo al pricing de línea y es un cambio más profundo, fuera de
alcance.

### S3 — Convivencia con el proyector contable (ADR 0030)

El proyector lee `retenciones_documento` (clave `retencion:{id}`, idempotente) y **no asienta el INC**
(`RETENCION_CUENTAS` sin `inc`). Se mantiene esa decisión (ADR 0030: incorporarlo descuadraría contra
las tablas operativas). El cableado inline solo hace que los renglones existan antes; proyectar tras él
cuadra (Σdébitos=Σcréditos), es idempotente (backfill replay sin duplicar) y el INC no genera asiento
—verificado por test.

**Sin migración nueva:** todo el cambio reusa `config_retenciones`/`retenciones_documento` (0032/0033).
