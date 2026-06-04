# Modo offline y sincronización (PWA)

> El POS debe seguir vendiendo sin internet. Decisión en `docs/adr/0004-offline-first-pwa.md`; endpoint en `api-contract.md` (`POST /ventas/sync`).

## Qué funciona offline

| Operación | Offline | Nota |
|---|---|---|
| Registrar venta | Sí | Se encola y sincroniza al reconectar |
| Consultar productos/precios | Sí | Desde un snapshot local del catálogo |
| Registrar gasto | Sí | Se encola |
| Abrir/cerrar caja | Limitado | Permitido; el arqueo se concilia al sincronizar |
| Emitir factura electrónica | **No** | Requiere DIAN online; se **difiere** y se emite tras sincronizar |
| Reportes / históricos | Solo lo cacheado | — |

## Cliente (PWA)

- **Service worker** cachea la app y un **snapshot del catálogo** (productos, precios, stock de referencia) para vender sin red.
- Cola en **IndexedDB**: cada operación lleva `idempotency_key` (uuid generado en el cliente), `timestamp` local y un número de secuencia monótono.
- Estado visible: cada venta offline se muestra como "pendiente de sincronizar" hasta confirmar.

## Sincronización

1. Al recuperar conexión, el cliente envía la cola en lote: `POST /api/v1/ventas/sync` con un array de operaciones (cada una con su `idempotency_key`).
2. El servidor procesa **idempotentemente**: por cada ítem responde `aplicada` / `duplicada` (misma key ya procesada) / `conflicto`.
3. El **consecutivo** y la fecha oficial los asigna el **servidor** al sincronizar (no offline), para evitar colisiones entre dispositivos.
4. Si la venta tenía factura pendiente, se encola la emisión DIAN después de aplicarse.

## Conflictos de stock (regla clave)

- La venta física **ya ocurrió** offline: no se rechaza. Si al sincronizar el stock es insuficiente, la venta **se acepta igual** y el stock puede quedar en negativo, **marcado para revisión** (alerta + `AJUSTE` posterior para conciliar).
- Esto prioriza no perder ventas reales; la conciliación de inventario es un paso administrativo, no un bloqueo.

## Idempotencia y seguridad

- El backend deduplica por `idempotency_key` (UNIQUE en `ventas`). Reintentos de red no duplican.
- Si el JWT expiró mientras estaba offline, al reconectar se renueva (refresh token) antes de sincronizar.
- Orden: el servidor respeta el orden de la secuencia del cliente dentro de un mismo dispositivo.

## Implicaciones para el backend

- Endpoints de mutación **idempotentes** y tolerantes a reintentos.
- Permitir stock negativo con marca (no `CHECK >= 0` duro en ventas; el control es por alerta/kardex).
- `POST /ventas/sync` reutiliza la misma lógica de `POST /ventas`, en lote.
