#!/bin/sh
# Entrypoint único: ramifica por SERVICE_TYPE (api | bot | worker). La MISMA imagen sirve los 3.
# `exec` para que el proceso herede PID 1 y reciba las señales de Railway (SIGTERM → shutdown limpio).
set -e

# Passthrough: si llegan argumentos (p. ej. el preDeployCommand de Railway o un start-command
# override: migraciones, one-offs), ejecútalos directamente. El arranque normal de api/worker corre
# SIN args → cae al ramificado por SERVICE_TYPE de abajo, sin cambios.
[ "$#" -gt 0 ] && exec "$@"

SERVICE_TYPE="${SERVICE_TYPE:-api}"

case "$SERVICE_TYPE" in
  api)
    # API + SPA del dashboard. uvloop por uvicorn[standard]; Railway inyecta PORT.
    exec uvicorn apps.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --loop uvloop
    ;;
  bot)
    # Servicio bot de Telegram (webhook); apps.bot.main escucha en PORT.
    exec python -m apps.bot.main
    ;;
  worker)
    # Worker ARQ (emisión DIAN asíncrona) sobre Redis.
    exec arq apps.worker.main.WorkerSettings
    ;;
  *)
    echo "docker-entrypoint: SERVICE_TYPE desconocido: '$SERVICE_TYPE' (usa: api | bot | worker)" >&2
    exit 1
    ;;
esac
