# Testing

> **Cadencia:** los tests se escriben y corren **al cierre de cada fase**, no test-primero. Dentro de la fase se implementa con libertad; al terminar, corre toda la suite y se corrige lo que falle.
>
> **Excepción TDD (invariantes críticos):** para aislamiento multi-tenant, idempotencia y "nada mueve stock/caja sin movimiento" sí se va **test-primero** (RED-GREEN-REFACTOR, alineado con el plugin `superpowers`). Es el alcance donde el TDD obligatorio del plugin gana; fuera de él manda la cadencia código-primero. Ver `.claude/rules/development-workflow.md`.
- **Unitarios** sobre la capa de servicios (lógica pura, sin BD).
- **Integración** sobre repositorios contra una base efímera.
- **End-to-end** de flujos críticos: venta, cierre de caja, emisión DIAN, provisioning de empresa.
- **Aislamiento multi-tenant:** una prueba que verifique que la empresa A nunca ve datos de la empresa B.
- **Migraciones:** probar que `upgrade`/`downgrade` corren limpio en control y en tenant.
- Cobertura razonable en módulos de dominio; correr `pytest` antes de mezclar a `main`.
- Idempotencia: tests que reintenten una venta/emisión y verifiquen que no se duplica.
