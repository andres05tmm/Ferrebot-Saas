# ADR 0002 — ORM SQLAlchemy + Alembic

- Estado: Aceptada
- Fecha: 2026-06

## Contexto
FerreBot usa psycopg2 con SQL crudo. El proyecto nuevo quiere un acceso a datos estándar y migraciones versionadas, manteniendo PostgreSQL.

## Decisión
Usar **SQLAlchemy** (ORM) y **Alembic** (migraciones), con dos árboles de migración: `control/` y `tenant/`.

## Consecuencias
- (+) Acceso a datos estándar y testeable; migraciones versionadas y reversibles; el ORM permite SQL crudo (`text()`) para portar módulos puntuales (MATIAS, bypass) sin reescribir todo de golpe.
- (-) Hay que escribir los modelos; aplicar migraciones a todas las empresas requiere un runner.
- PostgreSQL no cambia; solo cambia cómo se consulta desde Python.
