# Changelog

Formato basado en Keep a Changelog. Versionado semántico.

## Compras: el ciclo completo (2026-07-25)

- Un solo tab **Compras**: la compra se registra al hacer el pedido (productos, cantidad y costo
  unitario obligatorios) con su forma de pago; el cronómetro mide cuánto tarda cada proveedor. El
  tab "Pedidos a proveedor" se fusionó aquí (ADR 0034).
- **Corrección de una compra recibida** (`POST /compras/{id}/corregir`): aplica las diferencias como
  movimientos AJUSTE, re-pondera el costo y concilia la deuda y la caja.
- **De dónde sale la plata** en cada pago (caja / efectivo guardado / banco). Los abonos a
  proveedor por fin postean su egreso cuando salen del cajón, y `flujo-dinero` desglosa los egresos
  por procedencia.
- **Pago mixto a proveedor** (migración **0068**): una parte en efectivo y otra por transferencia, al
  pedir, al recibir o al abonar. Solo la parte del cajón mueve la caja; el flujo de dinero muestra
  aparte lo pagado por fuera de ella.
- Migraciones tenant **0067** y **0068**.

## [Unreleased]

### Added
- Andamiaje inicial del proyecto: estructura, reglas, docs y ADRs.
- Plan de arquitectura SaaS multi-empresa (DB por empresa). Ver `docs/architecture.md`.
- Specs que desbloquean el código: `docs/schema.md` (esquema detallado), `docs/api-contract.md` (endpoints v1), `docs/tenancy.md` (multi-tenancy a fondo).
- Capacidades por empresa (feature flags) para módulos fiscales/contables opcionales: `docs/feature-flags.md`.
- Specs de media prioridad: `docs/auth-rbac.md`, `docs/secrets.md`, `docs/offline-sync.md`, `docs/facturacion-dian.md`.
