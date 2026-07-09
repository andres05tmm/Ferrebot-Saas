# ADR 0031 — Pedidos a proveedor con cronómetro de lead time e inventario progresivo

- **Estado:** aceptado (2026-07-09)
- **Contexto:** reforma del dashboard POS (Punto Rojo). El dueño quiere medir cuánto tarda cada
  proveedor en traer la mercancía desde que se hace el pedido, con todo conectado: inventario,
  deuda al proveedor, abonos/pagos y anticipos. Además, el negocio es familiar (2 personas): no
  puede hacer un conteo general de inventario, así que necesita un mecanismo para volver el
  inventario confiable *paulatinamente*.

## Decisión

1. **Módulo nuevo `modules/pedidos_proveedor/`** (tablas `pedidos_proveedor` +
   `pedidos_proveedor_detalle`, migración 0052; feature `pedidos_proveedor`, dep `inventario`).
   NO se toca `modules/pedidos` (domicilios de cliente, ADR 0016). El cronómetro es
   `fecha_recepcion − fecha_pedido`, **derivado en lectura** (nunca columna).

2. **Captura FLEXIBLE** (decisión del dueño, 2026-07-09): el pedido se registra rápido
   (proveedor + descripción + monto estimado; líneas opcionales) y lo preciso —productos,
   cantidades y costos REALES— se fija al recibir la mercancía.
   > **Reversible:** si a papá/la empleada les resulta más cómodo capturar productos desde el
   > inicio, el esquema ya lo soporta (`lineas[]` en el alta); solo cambiaría el énfasis de la UI.
   > Preguntarles tras unas semanas de uso.

3. **Recepción transaccional** (`POST /pedidos-proveedor/{id}/recibir`, key natural
   `pedido-recibo:{id}`): en UNA transacción (a) la compra real vía `ComprasService.registrar`
   (ENTRADA + costo promedio, ADR 0025); (b) la deuda en `facturas_proveedores` si es a crédito;
   (c) el pago de contado como **egreso de caja** (no gasto: mercancía ≠ gasto operativo);
   (d) el cuadre de inventario. Recibir dos veces con la misma sustancia = replay; con números
   distintos = 409.

4. **Anticipos** (hay proveedores que cobran al pedir): `anticipo` en el alta egresa de la caja
   (key `pedido-anticipo:{id}`). Al recibir `anticipado`: si costó más que el anticipo, el
   remanente DEBE tener destino (caja o crédito); si va a crédito, la factura nace por el total
   con un **abono automático del anticipo** (pendiente = lo que de verdad se debe). Cancelar un
   pedido con anticipo NO revierte el dinero (ya está en manos del proveedor): queda nota visible.

5. **Puente compra→CxP directa:** `POST /compras` acepta `a_credito` (+`numero_factura`,
   `fecha_vencimiento`) y crea la cuenta por pagar en la misma transacción (antes eran dos flujos
   manuales desconectados).

6. **Inventario progresivo:** columna `inventario.cuadrado_at` (0052). El negocio arranca sin
   inventario y vende en negativo (modo permisivo, ya default). El cuadre ocurre al ritmo de las
   compras: al recibir un producto que se había acabado, el físico es conocido (= lo que llegó) →
   `cantidad_fisica` en la línea de recepción fija el stock (conteo set-to-absolute vía
   `InventarioService.contar`, movimiento AJUSTE trazable — regla #7) y sella `cuadrado_at`.
   Todo conteo físico (también el manual de `/inventario/conteo`) sella la marca; el AJUSTE
   relativo no. Los reportes distinguen productos cuadrados (confiables) de no cuadrados.

7. **Aviso de pedido demorado** (fase posterior): cron `avisos_pedidos_proveedor` con dedup en
   `pedidos_proveedor.ultimo_aviso_at` (patrón `avisos_pagar`, ADR 0019).

## Consecuencias

- Lead time por proveedor medible desde el primer pedido; el semáforo del dashboard compara el
  pedido en camino contra el promedio histórico del proveedor (o su fecha estimada).
- El dinero al proveedor queda 100% trazado: anticipo (egreso) + remanente (egreso o CxP con
  abono automático); nada se pierde del registro.
- El inventario se vuelve confiable producto a producto sin jornadas de conteo; `cuadrado_at`
  habilita que stock bajo / valor de inventario / sugerido de compra (futuro) solo miren
  productos confiables.
- La recepción es una transacción larga (compra + factura + caja + cuadres) sobre una sesión de
  tenant: aceptado (volúmenes de mostrador), sin llamadas HTTP internas.
