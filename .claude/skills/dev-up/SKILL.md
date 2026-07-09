---
name: dev-up
description: Levantar el entorno de desarrollo local completo - Docker (ferrebot-pg/ferrebot-redis), API uvicorn, dashboard vite y dev_token con sesión iniciada. Usar cuando el usuario diga "levanta el entorno", "arranca el dev", "quiero ver el dashboard local", o antes de cualquier trabajo que necesite la app corriendo.
---

# /dev-up — entorno dev listo con sesión iniciada

Guía canónica de referencia: `docs/DEV-LOCAL.md`. Ejecutar en orden; no saltar la espera de Postgres.

## 1. Docker

```bash
docker ps --filter name=ferrebot --format "{{.Names}} {{.Status}}"
```

- Si Docker Desktop no responde, avisar al usuario que lo abra (tarda ~1 min) y esperar.
- `ferrebot-redis` levanta solo (`--restart unless-stopped`); `ferrebot-pg` **necesita arranque manual tras reiniciar el PC**:

```bash
docker start ferrebot-pg ferrebot-redis
# esperar a que Postgres acepte conexiones (puerto 5433):
pg_isready -h localhost -p 5433   # repetir hasta "accepting connections"
```

## 2. API (background)

```bash
.venv/Scripts/python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Correr desde la raíz del repo (lee `.env`). Verificar: `curl -s localhost:8000/health` → `{"status":"ok"}`.
Si faltan dependencias: `uv sync --extra dev`.

## 3. Dashboard (background)

```bash
cd dashboard && npm run dev    # vite en http://localhost:5173
```

- Requiere `dashboard/.env.local` con `VITE_TENANT_SLUG=<slug>` (default `clinica-demo`). Si no existe, crearlo.
- Vite proxya `/api/v1` → `localhost:8000`.
- Si el tenant local no existe aún: `.venv/Scripts/python.exe -m tools.seed_clinica_demo` (idempotente).

## 4. Sesión iniciada (dev_token)

```bash
.venv/Scripts/python.exe -m tools.dev_token [slug]   # default clinica-demo
```

Imprime una línea JS (`localStorage.setItem('ferrebot_token',...)`) para pegar en la consola del navegador en http://localhost:5173. El token dura 12h.

## 5. Entrega

Reportar al usuario: URL del dashboard (http://localhost:5173), API arriba (health ok), tenant activo, y el snippet del token listo para pegar. Si algo no levantó, decir exactamente qué y por qué.
