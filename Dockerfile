# FerreBot SaaS — imagen ÚNICA para los 3 servicios (api · bot · worker).
# Difieren solo por la variable de entorno SERVICE_TYPE (ver docker-entrypoint.sh).
# Reproducible: deps Python por `uv.lock` (--frozen); dashboard por `npm ci` (package-lock.json).
# Sin secretos horneados: todo se inyecta por env en runtime.

# ── Stage 1 — build del dashboard (Vite) ─────────────────────────────────────
FROM node:20-slim AS dashboard
WORKDIR /dashboard

# Deps primero (capa cacheable): solo cambian al tocar package*.json.
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci

# Código del dashboard + build de producción.
COPY dashboard/ ./
# Único valor "horneado" en el bundle: el bot de Telegram del widget de login (deploy lean = bot de PR).
# El build de prod usa /api/v1 relativo y resuelve la empresa por subdominio (sin X-Tenant-Slug).
ARG VITE_TELEGRAM_BOT_USERNAME=""
ENV VITE_TELEGRAM_BOT_USERNAME=${VITE_TELEGRAM_BOT_USERNAME}
RUN npm run build   # → /dashboard/dist

# ── Stage 2 — runtime Python (api/bot/worker) ────────────────────────────────
FROM python:3.12-slim AS runtime

# uv pinneado (binario distroless oficial) para instalar deps de forma reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Deps Python (capa cacheable): se resuelven desde el lock, sin instalar el proyecto ni las dev-deps.
# El código se importa por PYTHONPATH=/app (no se empaqueta), así `apps/api/main.py` resuelve
# dashboard/dist en la raíz del repo dentro de la imagen.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Código de la aplicación (solo lo que corre en runtime).
COPY core/ ./core/
COPY apps/ ./apps/
COPY modules/ ./modules/
COPY ai/ ./ai/
COPY tools/ ./tools/
COPY migrations/ ./migrations/

# Dashboard ya compilado (servido por el API como SPA).
COPY --from=dashboard /dashboard/dist ./dashboard/dist

# Entrypoint que ramifica por SERVICE_TYPE.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# El API escucha en 8000 por defecto; Railway inyecta PORT en runtime.
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
