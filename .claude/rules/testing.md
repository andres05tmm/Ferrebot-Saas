# Testing

- **Unitarios** sobre la capa de servicios (lógica pura, sin BD).
- **Integración** sobre repositorios contra una base efímera.
- **End-to-end** de flujos críticos: venta, cierre de caja, emisión DIAN, provisioning de empresa.
- **Aislamiento multi-tenant:** una prueba que verifique que la empresa A nunca ve datos de la empresa B.
- **Migraciones:** probar que `upgrade`/`downgrade` corren limpio en control y en tenant.
- Cobertura razonable en módulos de dominio; correr `pytest` antes de mezclar a `main`.
- Idempotencia: tests que reintenten una venta/emisión y verifiquen que no se duplica.
