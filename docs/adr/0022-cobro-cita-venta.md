# ADR 0022 — Cobrar una cita crea una venta (puente agenda → contabilidad)

- **Estado:** Aceptado
- **Fecha:** 2026-07-01
- **Relacionados:** ADR 0021 (partición del pack `pos`), ADR 0014 (documento por venta), ADR 0006 (agenda)

## Contexto

Con la partición del pack `pos` (ADR 0021), un negocio de servicios (peluquería, spa, hotel) puede
activar su contabilidad (`caja` + `ventas`) junto a `pack_agenda`. Pero agenda y caja eran packs
separados sin enlace: al atender una cita, el cobro había que registrarlo aparte (o no se registraba).
Para que el software sea *contable* para servicios, el cobro de la cita debe producir el registro
contable — sin violar "nada modifica caja sin movimiento de caja" ni la idempotencia.

## Decisión

### D1 — El cobro crea una VENTA; no postea `caja_movimientos`

La fuente única contable del cobro es una fila en `ventas`. El arqueo de caja es **híbrido**
(`modules/caja/arqueo.py`): `esperado = saldo_inicial + ventas_efectivo (tabla ventas) + ingresos −
egresos (caja_movimientos)`. Una venta en efectivo **ya** cuadra la caja por `ventas_efectivo`;
insertar además un movimiento de caja doble-contaría (guardrail documentado en `calcular_arqueo`).

### D2 — La línea es "varia": el servicio NO es producto del catálogo (v1)

La venta se crea con una línea varia (`producto_id=None`, `descripcion="{servicio} — cita #{id}"`,
`precio_unitario` = `servicios.precio` — con `precio_override` opcional —, `iva=0` por defecto).
La línea varia no descuenta stock **por construcción** (`descontar_stock=False`,
`modules/ventas/service.py::_linea_varia`): el invariante "nada mueve stock sin movimiento" queda
intacto sin código nuevo. Evolución futura si hace falta facturar servicios con IVA/catálogo:
`productos.es_servicio`.

### D3 — Idempotencia doble

1. `ventas.idempotency_key = "cita-cobro:{cita_id}"` — el replay de `registrar_venta` ya existe:
   reintentar devuelve la MISMA venta con `replay=true`.
2. `citas.venta_id` (FK a `ventas`, UNIQUE, NULL) — el vínculo se escribe en la **misma transacción**
   que la venta, con la cita tomada bajo `SELECT … FOR UPDATE`. Dos cobros concurrentes: uno gana,
   el otro ve `venta_id` ya puesto y devuelve la misma venta como replay.

### D4 — Estados

Solo se cobra una cita en `pendiente` o `confirmada` (o `cumplida` sin venta vinculada: atendida y
cobrada después). Al cobrar, la cita pasa a `cumplida` y guarda `cobrada_en`. `cancelada`/`no_show`
→ 409. Cobrar una cita ya cobrada → 200 con `replay=true` (no error: reintentos de red).

### D5 — Factura si aplica: puerto de cierre fiscal existente, best-effort

Tras el commit del cobro se invoca `encolar_cierre_pos` (`modules/facturacion/pos_hook`, ADR 0014):
según capacidades del tenant rutea POS electrónico / FE / nada. Corre FUERA de la transacción del
cobro y **nunca lo rompe** (mismo contrato que en el router de ventas).

### D6 — Capacidades y alcance v1

- El endpoint vive en el router de agenda (`pack_agenda`) y exige además `require_feature("ventas")`.
- `metodo_pago`: efectivo | transferencia | datafono. **`fiado` queda fuera de v1** (requiere
  cliente_id del POS; la identidad de agenda es el teléfono).
- Reservas (hotel): una reserva ES una cita — el mismo endpoint sirve; el precio por noches se pasa
  con `precio_override` (v1; el cálculo automático por noches queda para cuando se necesite).
- El vendedor que cobra es el usuario autenticado del dashboard (rol `vendedor`+).

## Consecuencias

- La peluquería marca "Cobrar" en la cita → venta registrada, caja cuadrada al cierre, factura si
  la tiene activa. Cero doble digitación.
- El historial de ventas del tenant de servicios muestra sus cobros de citas (línea varia con la
  descripción del servicio) — coherente con `/historial` visible con `ventas` (ADR 0021).
- Migración tenant `0027_citas_cobro`: `citas.venta_id` + `citas.cobrada_en`, aditiva y NULL-safe
  para todos los tenants existentes.
