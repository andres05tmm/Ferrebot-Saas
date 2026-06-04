# ADR 0001 — Multi-tenancy: una base de datos por empresa

- Estado: Aceptada
- Fecha: 2026-06

## Contexto
El sistema se vende a varias empresas; cada una maneja datos fiscales y financieros sensibles (DIAN). Se evaluaron tres modelos: BD compartida con `tenant_id` + RLS, esquema por empresa, y base por empresa.

## Decisión
**Una base de datos por empresa** (DB-per-tenant) + un control DB global con el registro de empresas, planes, secretos y branding.

## Consecuencias
- (+) Máximo aislamiento; backups y restauración por empresa; radio de impacto acotado; esquema de negocio limpio (sin columna de empresa).
- (-) Migraciones sobre N bases (mitigado con un runner), provisioning más complejo (automatizado), y riesgo de agotar conexiones de Postgres -> obligatorio **PgBouncer** (ver ADR 0004... ver `runbook.md`).
- Escala: muchas bases en una instancia al inicio; instancias dedicadas para clientes grandes (transparente vía el control DB).
