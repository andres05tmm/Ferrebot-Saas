# ADR 0030 — Motor contable: ledger de doble partida + PUC (Fase 8)

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0025 (COGS por promedio ponderado + `fecha_operacion`), ADR 0026 (notas/
  devoluciones), ADR 0027 (retenciones/INC/libros), ADR 0028 (conciliación/CxP), ADR 0022 (arqueo
  híbrido), reglas no negociables #2 (datos solo por repositorios), #4 (TZ Colombia), #7/#8
  (movimientos e idempotencia), multi-tenancy (DB por empresa).

## Contexto

Tras las fases contables A–D el sistema tiene COGS por promedio, devoluciones, retenciones y
conciliación, pero **sin doble partida**: los reportes (`modules/reportes`) se derivan al vuelo de las
tablas operativas y no existe un libro diario ni un PUC. Para un negocio formal colombiano falta un
**motor contable** que produzca balance general, estado de resultados y flujo de efectivo desde un
libro mayor auditado, con períodos y cierres.

Restricciones duras: **no romper el arqueo híbrido de caja** (ADR 0022: ventas efectivo se leen de
`ventas`, egresos de `caja_movimientos`), **opt-in** (apagado por defecto), y **aislamiento por
empresa** (la base ES la frontera, sin `empresa_id`).

## Decisión

Patrón adoptado: **Odoo `account.move` + Modern Treasury** — asientos inmutables *append-only*,
corrección por reversión (no edición), validación de cuadre en app-layer.

### D1 — Modelo de datos (migraciones tenant 0037-0041)

Cinco tablas nuevas, cadena lineal desde `0036_gastos_cxp`:

- **`puc_cuentas`** (0037): árbol con `parent_id`, `codigo` único, `naturaleza` (`debito|credito`) e
  `imputable` (solo las hojas reciben movimientos). La semilla del PUC colombiano a nivel de comercio
  (clases 1-6: caja/bancos, clientes-fiado, anticipos de retención, inventario, proveedores, IVA
  generado/descontable, retenciones por pagar, patrimonio, ingresos, devoluciones en ventas, gastos por
  categoría, costo de ventas) **no va en la migración**: se siembra opt-in por el servicio
  (`asegurar_puc`, idempotente) al habilitar la feature, para no inflar la base de tenants que no usan
  el ledger.
- **`periodo_contable`** (0038): períodos mensuales `open|locked|closed`; un período no-`open` rechaza
  el posting.
- **`journal_entry`** (0039): cabecera con `fecha`, `periodo_id`, `estado` (`pending|posted`),
  `origen_tipo`+`origen_id`, `idempotency_key` UNIQUE, `reverso_de`. **Inmutable una vez `posted`.**
- **`journal_line`** (0040): `direction` (`debit|credit`) con `amount` sin signo (`> 0`, CHECK). El
  cuadre débitos=créditos se valida en **app-layer** antes de postear (con la naturaleza a la vista); la
  base solo garantiza el signo.
- **`saldo_cache`** (0041): saldo por (cuenta, período), mantenido incrementalmente al postear y
  **recomputable** desde `journal_line` (`recomputar_saldos`). Es caché: su verdad son las líneas.

### D2 — El proyector: evento operativo → un asiento idempotente

`modules/contabilidad/proyector.py` traduce cada evento a UN asiento, con la **clave del evento** como
`idempotency_key` (`venta:{id}`, `gasto:{id}`, `devolucion:{id}`, …): reproyectar el mismo evento
devuelve el mismo asiento (replay), nunca duplica. Eventos cubiertos y sus asientos:

| Evento | Débito | Crédito |
|---|---|---|
| **Venta contado** | Caja/Bancos (total) · Costo de ventas (COGS) | Ingresos (subtotal) · IVA generado · Inventario (COGS) |
| **Venta fiado** | Clientes (total) · Costo de ventas | Ingresos · IVA generado · Inventario |
| **Abono de fiado** | Caja | Clientes |
| **Gasto** | Gasto por categoría | Caja |
| **Compra** | Inventario (base) · IVA descontable | Proveedores (total) |
| **Abono a proveedor** | Proveedores | Caja |
| **Devolución** | Devoluciones en ventas (base) · IVA generado · Inventario (COGS) | Caja/Clientes (total) · Costo de ventas (COGS) |
| **Retención (venta)** | Anticipo de retención (activo) | Caja/Clientes |
| **Retención (compra)** | Proveedores | Retención por pagar (pasivo) |

El costo (COGS) se toma del **snapshot** de la `SALIDA`/`DEVOLUCION` (`movimientos_inventario.
costo_unitario`, ADR 0025/0026), no del promedio del día. La lectura de las tablas operativas vive en
`fuente_repository.py` (capa de repositorio del proyector; solo lee, nunca muta el origen). El
**backfill** proyecta solo hacia adelante desde una fecha, idempotente.

### D3 — Cuadre, inmutabilidad, período (invariantes, TDD test-primero)

`LedgerService.registrar_asiento` (a) cuantiza (`core.money.cuantizar`), (b) exige Σdébitos=Σcréditos
o `AsientoDescuadrado`, (c) resuelve la cuenta imputable o `CuentaInexistente/NoImputable`, (d) resuelve
el período de la `fecha` y rechaza si no está `open` (`PeriodoBloqueado`), (e) postea. Un asiento
`posted` **no se edita** (`anexar_linea` → `AsientoInmutable`): la corrección es `reversar`, que crea un
asiento espejo con las direcciones invertidas y `reverso_de` apuntando al original. Tests test-primero:
descuadre rechazado, inmutabilidad + espejo, período bloqueado, proyector idempotente, aislamiento
multi-tenant, y **invariancia del arqueo** (dos tenants con los mismos movimientos, uno con ledger
proyectado, arrojan idéntico `saldo_esperado`/`diferencia`).

### D4 — El ledger es capa DERIVADA: no alimenta el arqueo

Las tablas del ledger son de solo-escritura por el proyector; **ningún** flujo operativo (venta, caja,
fiado) las lee. El arqueo híbrido sigue leyendo `ventas` + `caja_movimientos` sin cambio. Ledger y
arqueo **se concilian por reporte**, no por acoplamiento. El backfill es "solo hacia adelante" + un
asiento de apertura con saldos iniciales (el patrimonio como partida de cierre).

### D5 — Estados financieros derivados

`estados.py` agrega `journal_line` (posted) por clase de cuenta: **balance de comprobación** (cuadra
por construcción), **estado de resultados** (clase 4 ingresos − clase 6 costos − clase 5 gastos),
**balance general** (activos = pasivos + patrimonio + utilidad del ejercicio como cierre) y **flujo de
efectivo** (movimientos de Caja+Bancos por origen). El P&L simple (`modules/reportes`) convive y sirve
de **validación cruzada**.

### D6 — Superficie y gating

Router `/contabilidad/*` (admin), gateado por la nueva feature **`contabilidad_ledger`** (OR sobre
`ventas`/`caja`), apagada por defecto → 404 sin el flag. Consulta de asientos, balance de comprobación
y estados; más dos acciones de operación (sembrar PUC, backfill).

## Decisiones no obvias

- **Contra-ingreso para devoluciones (cuenta 417505).** La devolución **no** debita `413505` (ingresos)
  sino una cuenta de devoluciones (naturaleza débito). Así el ingreso del ledger (413505) coincide
  **exactamente** con el P&L simple —que no reversa ingresos por devolución (ADR 0026 D4)—, mientras el
  COGS sí neta (venta − devolución) en ambos. El cruce cierra al centavo en ingresos, costo de ventas y
  gastos.
- **IVA de la devolución a prorrata.** `devoluciones_detalle` no guarda la tarifa de IVA; se reversa el
  IVA proporcional al ratio `impuestos/total` de la venta origen (redondeo único). En una devolución
  total el ratio es 1 → reversa exacta.
- **La compra se asienta contra Proveedores (CxP).** `compras` no lleva método de pago; asentar contra
  Caja haría divergir el efectivo del ledger de la realidad (las compras no postean `caja_movimientos`).
  El financiamiento queda en Proveedores y se concilia aparte. Un gasto que **salda** una CxP
  (`abono_proveedor_id`, ADR 0028 D5) se **omite** en la proyección de gastos: su pago lo asienta el
  abono a proveedor (evita doble conteo).
- **INC no se asienta en v1.** Como el INC no incrementa el total cobrado (ADR 0027 D5), incorporarlo
  descuadraría contra las tablas operativas. Se registra en `retenciones_documento` pero no genera
  asiento (documentado, opt-in futuro).
- **Enums como TEXT + CHECK,** no enums PG (criterio de `devoluciones`/`retenciones`): naturaleza,
  direction, estado y período.

## Consecuencias

- El negocio obtiene libro diario/mayor auditado, PUC editable por empresa, períodos con candado y
  estados financieros derivados, sin tocar el arqueo ni los flujos operativos.
- Todo asiento cuadra (invariante) → el balance de comprobación cuadra y el balance general cierra por
  construcción. El cruce con el P&L simple coincide al centavo en ingresos/costo/gastos.
- Migraciones tenant **0037-0041**, aditivas, con `downgrade` limpio (verificado ciclo
  base→head→base→head contra base local).
- **Diferencias de conciliación legítimas** (ledger vs P&L/arqueo, documentadas): la utilidad del
  estado de resultados resta las devoluciones (contra-ingreso 417505) que el P&L simple no resta; un
  gasto que salda CxP es pago de pasivo en el ledger pero cuenta como gasto en el P&L simple; la
  retención reduce el efectivo del ledger pero no el arqueo operativo.
- **Cabos sueltos:** (a) el asiento de apertura desde saldos iniciales reales (inventario valorizado,
  cartera, CxP) queda como helper explícito, no automático desde un corte; (b) cierre de período
  (asiento de cierre que lleva 4/5/6 a patrimonio) no implementado —los estados calculan la utilidad al
  vuelo—; (c) las facturas de proveedor "sueltas" (`facturas_proveedores` sin `compra`) no se proyectan
  como CxP en v1 (sus abonos sí debitan Proveedores).
