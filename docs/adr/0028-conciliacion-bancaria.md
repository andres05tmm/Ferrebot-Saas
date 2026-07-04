# ADR 0028 — Conciliación bancaria + gastos↔cuentas por pagar (Fase 5 Contable D)

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0019 (pack pagar — avisos de CxP), ADR 0025 (ORM de `bancolombia_transferencias`),
  regla no negociable #7 (nada mueve stock/caja sin movimiento) y #8 (idempotencia)

## Contexto

El SaaS ya registra los movimientos internos de plata (ventas por transferencia, gastos de caja,
abonos a proveedores) y, desde la 0001, guarda las transferencias entrantes de Bancolombia parseadas
de Gmail (`bancolombia_transferencias`, con modelo ORM desde ADR 0025). Faltaban dos piezas contables:

1. **Conciliación bancaria:** cruzar el extracto del banco contra los movimientos internos para saber
   qué está registrado y qué no. Sin esto, el dueño no tiene forma sistemática de cuadrar el banco.
2. **Gastos ↔ CxP:** un pago a proveedor hecho por caja/banco (un `gasto`) no tenía forma de **saldar**
   la cuenta por pagar (`facturas_proveedores`) sin registrar el abono a mano por separado — con el
   riesgo de contarlo dos veces.

## Decisión

### D1 — El libro de movimientos bancarios ES `bancolombia_transferencias` (extendida)

Se **adopta y extiende** la tabla existente (migración tenant **0035**) en vez de crear una nueva:

- `gmail_message_id` pasa a **NULLABLE** (la ingesta de un extracto no viene de Gmail); su UNIQUE se
  conserva (Postgres admite múltiples NULL), así el canal Gmail sigue idempotente por su id.
- `referencia_bancaria` (TEXT) + **índice UNIQUE parcial** `WHERE NOT NULL`: es el **ancla de
  idempotencia** de la ingesta del extracto.
- `naturaleza` (TEXT + CHECK `'credito'|'debito'`): un extracto trae créditos (entra plata) y débitos
  (sale). Las filas históricas de Gmail (entrantes) quedan `'credito'`. Se usó TEXT+CHECK (no un enum
  PG) por ser binario y para no proliferar tipos.
- `estado_conciliacion` (enum PG `conciliacion_estado`): `no_conciliado → sugerido → conciliado`.
- `conciliado_con_tipo`/`conciliado_con_id`: **enlace polimórfico FK-less** (mismo criterio que
  `ventas.vendedor_id` o `FacturaElectronica.venta_id`: no acoplar el grafo de mappers entre módulos)
  al movimiento interno — `tipo ∈ {venta, gasto, abono}`.
- `conciliado_en`: sello de la confirmación explícita.

### D2 — Ingesta idempotente por referencia bancaria

`SqlBancosRepository.ingestar_uno` inserta con `INSERT … ON CONFLICT (referencia_bancaria) DO NOTHING`
sobre el índice UNIQUE parcial: reprocesar el mismo extracto **no duplica** movimientos, aun bajo
reintentos/concurrencia (no es un check-then-insert con ventana de carrera). Invariante cubierto por
`test_ingesta_idempotente_por_referencia`.

### D3 — Match semi-automático determinista; los ambiguos JAMÁS se auto-concilian

`BancosService.sugerir_pendientes` recorre los `no_conciliado` y busca candidatos internos por
**monto + fecha**, acotado por `naturaleza`:

- `credito` → ventas con `metodo_pago='transferencia'` y `estado='completada'`.
- `debito` → `gastos` ∪ `facturas_abonos` (abonos a proveedores).

Solo si hay **exactamente un** candidato de alta confianza lo marca `sugerido`. **Regla dura:** con 0
o ≥2 candidatos el movimiento queda `no_conciliado` y sus candidatos se **listan** para resolverlos a
mano — nunca se auto-concilia un monto ambiguo (`test_montos_ambiguos_nunca_se_autoconcilian`). La
confirmación (`no_conciliado`/`sugerido` → `conciliado`) es **explícita** (`confirmar`), y valida que
el enlace elegido sea un candidato real (422 si no). Los internos ya tomados por **otra** fila
bancaria se excluyen de los candidatos (`bt.id IS DISTINCT FROM :self_id`), para no ofrecer el mismo
movimiento interno dos veces.

Sobre "monto+fecha+**referencia**" del plan: la `referencia_bancaria` es el ancla de idempotencia de
la ingesta y un dato de despliegue; el **match determinista** usa monto+fecha (acotado por naturaleza),
porque los movimientos internos no cargan la referencia del banco. Cualquier colisión cae en la regla
de ambigüedad.

### D4 — Conciliar SOLO enlaza: no toca ningún saldo

Las transiciones `marcar_sugerido`/`confirmar` **solo escriben columnas de estado/enlace en la fila
bancaria**. No tocan `ventas`, `gastos`, `caja_movimientos`, `fiados` ni `facturas_proveedores`.
Invariante crítico con test explícito `test_conciliar_no_altera_saldos` (snapshot de saldos
antes == después de conciliar).

### D5 — Gastos ↔ CxP: un gasto salda una factura generando SU único abono

Migración tenant **0036**: `gastos` gana tres columnas nullable (aditivas, seguras sobre datos
existentes): `proveedor_id` (FK proveedores), `factura_proveedor_id` (FK facturas_proveedores) y
`abono_proveedor_id` (FK facturas_abonos).

**Semántica (no duplicar el abono):** al registrar un gasto con `factura_proveedor_id`, el
`CajaService` valida contra la CxP (factura existe; monto ≤ pendiente, reusando los errores de
`modules/proveedores`) y, tras insertar el gasto + su egreso de caja, crea **exactamente un**
`AbonoProveedor` (que recalcula `pendiente` por el flujo existente de proveedores) y guarda su id en
`gastos.abono_proveedor_id`. Ese id es el **candado anti-duplicación**: el gasto genera su propio
abono, no se registra otro aparte. La idempotencia del gasto (`idempotency_key`, chequeada **antes**
de crear el abono) garantiza que un replay devuelva el gasto previo **sin** crear un segundo abono
(`test_replay_idempotente_no_duplica_abono`).

Por qué no es doble conteo: el **egreso de caja** (libro de CAJA) y el **abono** (libro de CxP) son
dos vistas del **mismo pago**. El arqueo de caja cuenta el egreso una vez (fuente única
`caja_movimientos`); la CxP reduce `pendiente` una vez (un solo abono). El vínculo lo cablea **solo el
router HTTP de caja** (misma sesión → misma transacción); el canal del bot sigue registrando gastos
simples (los kwargs de enlace se pasan al repo solo cuando hay enlace).

### D6 — Gating y RBAC

Nueva feature opcional `conciliacion_bancaria` (catálogo), con dependencia (OR) en `caja` — su
superficie de contabilidad de caja (gastos) vive tras ese flag. El router `/bancos/*` responde 404 sin
el flag (como si no existiera) y es **todo de admin**: el cruce banco↔ventas/gastos/CxP es información
sensible del negocio (mismo criterio que `pack_pagar`).

## Consecuencias

- El dueño puede ingerir el extracto (idempotente), correr el match semi-automático y confirmar los
  enlaces de alta confianza, sin que nada de esto toque los saldos del negocio.
- Un gasto puede saldar una cuenta por pagar en un solo paso, sin registrar el abono aparte y sin
  doble conteo (un egreso de caja + un abono de CxP por pago).
- Migraciones tenant **0035–0036**, aditivas y con `downgrade` limpio (verificado `upgrade head` +
  `downgrade base` contra una base tenant local). La 0035 añade el enum `conciliacion_estado`
  (total de enums del esquema: 24 → 25).
- **Desviaciones vs. spec:** (a) el match usa monto+fecha (no la referencia bancaria, ausente en los
  internos — D3); (b) no se creó tabla nueva de conciliación: el enlace vive en la fila bancaria
  (`conciliado_con_*`), manteniendo la paridad de esquema sin tablas nuevas. Sin otras diferencias.
