"""Ingesta de transferencias Bancolombia por Gmail (push Pub/Sub → webhook → parse → notifica).

Port de `routers/bancolombia_notifier.py` del bot legacy, separado en capas para multi-tenant:
  - `parser`   — funciones PURAS (detección + extracción de campos); testeables sin red.
  - `cliente`  — cliente OAuth2 + Gmail API (history/messages/watch); tenant-agnóstico.
  - `ingesta`  — orquestador por tenant (historyId → mensajes → filtro → persistir → Telegram → SSE).
  - `webhook`  — endpoint global `POST /webhooks/bancolombia/{token}` (token opaco → tenant, fail-closed).
"""
