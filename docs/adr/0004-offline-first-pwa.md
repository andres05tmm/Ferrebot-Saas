# ADR 0004 — POS offline-first (PWA)

- Estado: Aceptada
- Fecha: 2026-06

## Contexto
Un POS en tienda debe seguir vendiendo si se cae el internet. Una app 100% nube deja a la ferretería sin vender ante un corte.

## Decisión
El dashboard es una **PWA** con **cola offline** (IndexedDB): registra ventas localmente sin conexión y **sincroniza al reconectar**, usando claves de **idempotencia** para no duplicar.

## Consecuencias
- (+) Continuidad de ventas ante cortes; mejor experiencia en tienda.
- (-) Complejidad de sincronización y resolución de conflictos; el backend debe ser idempotente y tolerar reintentos.
