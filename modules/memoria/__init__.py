"""Memoria conversacional del bot por empresa (tenant): historial + entidades recordadas.

Tablas (ya creadas en migrations/tenant/0001, NO se recrean):
  - `conversaciones_bot`  → historial de mensajes user/assistant por chat.
  - `memoria_entidades`   → último cliente / último producto por chat (scratch persistente).

Aislamiento por base (multitenancy.md): el repo opera sobre la sesión del tenant que llega
desde el TurnoHandler; sin `empresa_id` en las tablas. SQL solo en `repository.py` (regla #2).
"""
