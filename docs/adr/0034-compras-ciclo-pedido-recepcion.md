# ADR 0034 — Compras: un solo tab para el ciclo pedido → recepción → corrección

- **Estado:** aceptado (2026-07-25)
- **Contexto:** Punto Rojo tenía dos tabs desconectados. **Compras** registraba mercancía ya
  recibida (movía stock y costo promedio) sin ciclo de vida, sin edición y —lo grave— una compra de
  contado **no dejaba ningún rastro de dinero**: ni caja, ni gasto, ni deuda. **Pedidos a proveedor**
  (ADR 0031) tenía el cronómetro de lead time y la recepción transaccional, pero su feature estaba
  apagada en el tenant, así que el dueño nunca lo vio. Él pidió una sola pantalla donde la compra se
  registre **al hacer el pedido**, con productos, cantidades y costo unitario, con su forma de pago,
  editable si se digitó mal, y con la plata trazada.

## Decisión

1. **Un solo tab `/compras`.** `pedidos_proveedor` es el agregado del CICLO DE VIDA (pedido →
   recibido/cancelado, cronómetro derivado); `compras` sigue siendo el HECHO de la recepción (lo
   único que mueve inventario y costo promedio, ADR 0025). No se fusionan tablas: meterle un
   `estado` a `compras` envenenaría a todos sus lectores (cockpit, análisis de precios, libro IVA,
   resbalos, kárdex, proyector contable), que asumen "compra = mercancía que ya entró". La UI nunca
   dice "pedido vs compra": dice **En camino / Recibida**. El tab "Pedidos a proveedor" sale del nav.

2. **Captura COMPLETA al pedir** — revoca la "captura flexible" del ADR 0031 §2, que era
   explícitamente reversible: cada línea exige `producto_id`, `cantidad` y `costo_estimado` (el costo
   unitario acordado; el real se confirma al recibir).

3. **La forma de pago se declara al pedir** (`condicion_pago` en el alta): `contado` deriva el
   anticipo al valor completo del pedido, `anticipado` exige `0 < anticipo < total`, `credito` no
   admite anticipo. La recepción la hereda y puede corregirla (la realidad manda).

4. **De dónde sale la plata** (`origen_fondos`: `caja` | `efectivo_externo` | `banco`) reemplaza al
   booleano `anticipo_desde_caja`. Solo `caja` postea el egreso; el efectivo guardado de días
   anteriores y las transferencias quedan registrados con su procedencia **sin descuadrar el
   arqueo**. Aplica al pago del pedido, al de la recepción y a los abonos a cuentas por pagar — que
   hasta hoy NUNCA movían caja aunque se pagaran con el efectivo del cajón.

4.b **Pago MIXTO** (2026-07-26, tras el uso real): un pago puede repartirse entre medios — parte del
   cajón, parte por transferencia. `pagos_proveedor` (0068) guarda las partes (`origen` + `monto`)
   colgadas del hecho que las originó (pedido / compra / abono), espejando `ventas_pagos` (0053) del
   lado del dinero que sale. Solo la parte `caja` postea movimiento de caja; las partes deben sumar
   exactamente lo que se paga (si no, 422). El reporte de flujo separa lo pagado por fuera de la caja
   en su propia línea: salió del negocio, pero no del cajón.

5. **Corrección por diferencia** (`POST /compras/{id}/corregir`, admin): se manda el detalle final
   correcto y el servicio aplica solo los deltas. Cada cambio de cantidad deja su movimiento
   **AJUSTE** en el kárdex (regla #7), el costo del producto se re-pondera revirtiendo la línea vieja
   y re-aplicando la nueva, la cuenta por pagar sigue al nuevo total y —si se pide— la diferencia
   sale o entra de la caja. La key natural lleva el **contador de correcciones**
   (`compra-correccion:{id}:{n}:{producto}`): sin él la segunda corrección haría replay de la
   primera. Vive en `modules/compras` (no en pedidos) para que sirva también a las compras directas.

6. **Todo egreso con su procedencia:** `CajaService.registrar_movimiento` gana `referencia`
   (`pedido:{id}`, `compra:{id}`, `abono:{id}`, extendiendo el `gasto:{id}` que ya existía) y
   `GET /reportes/flujo-dinero` desglosa `egresos_por_origen` con etiquetas del dueño. Es un detalle
   de lo ya contado, no un sumando nuevo: no hay doble conteo.

7. **Nada de esto rompe al vertical construcción:** las compras imputadas a obra y los viajes de
   material no son pedidos de mercancía (no mueven stock, llevan resbalo). El tab viejo se conserva
   íntegro como `tabs/compras/PanelObra.jsx`: es la sección "De obra / viaje" en construcción y el
   fallback para los tenants sin la feature `pedidos_proveedor`.

## Consecuencias

- La plata que sale hacia un proveedor queda registrada siempre, con su origen: se acabó la compra
  de contado invisible. El arqueo solo se mueve cuando el dinero salió de verdad del cajón.
- El dueño puede corregir sin borrar ni re-crear: el kárdex cuenta la historia (ENTRADA + AJUSTE) y
  la deuda/caja se concilian solas.
- Corregir el costo re-pondera el promedio: es exacto si no hubo ventas entre la recepción y la
  corrección (el caso real, el error se ve el mismo día); con ventas de por medio queda una
  aproximación del mismo orden que cualquier promedio móvil. El COGS ya grabado no se reescribe.
- Las compras registradas sin pedido (histórico, bot, construcción) no tienen cronómetro ni forma de
  pago; siguen siendo corregibles. No se inventan pedidos retroactivos: falsearían el lead time.
- **Deuda conocida:** el proyector contable (ADR 0030) asienta *toda* compra contra Proveedores como
  si fuera a crédito. Con más compras pagadas al momento, el pasivo del ledger queda inflado. No
  explota solo (el backfill es manual), pero hay que pasarle la condición de pago.
- Fuera de alcance: anular una compra recibida, **recepción parcial** ("llegaron 8 de 10" — el
  candidato #1 del backlog), devolución al proveedor y re-proyección contable tras corregir.
