"""Loaders idempotentes de datos de pack (ADR 0007 fase 2): del manifiesto a la BD del tenant.

Driver SYNC (psycopg), igual que `provision_tenant` / `seed_clinica_demo`: no toca el caché de
engines async de la API. Cada loader hace UPSERT por claves naturales → re-ejecutar no duplica
(idempotencia, requisito DURO de .claude/rules/testing.md). El registro declarativo (`registry.py`)
mapea cada feature-flag a su loader, para que el provisionador (fase 3) itere solo los packs activos.
"""
