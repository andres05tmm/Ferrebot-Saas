# Correr el SaaS en local contra `clinica-demo`

Pasos exactos para levantar el backend + dashboard en tu máquina (Windows) y ver el pack **Agenda**
con datos de prueba, **sin** el Telegram Login Widget. Trabaja sobre el tenant de prueba
`clinica-demo` — **nunca Punto Rojo**.

> Prerrequisitos (ya los tienes): Docker Desktop, el venv `.venv` con las deps de Python, y las deps
> del dashboard (`cd dashboard && npm install`). El `.env` de la raíz ya apunta al Postgres local
> (`localhost:5433`) y a Redis (`localhost:6379`).

---

## 1. Levantar Postgres + Redis (contenedores Docker)

Con Docker Desktop abierto:

```powershell
docker start ferrebot-pg ferrebot-redis
docker ps --filter name=ferrebot   # ambos en "Up": pg → 5433, redis → 6379
```

`ferrebot-pg` (Postgres, `localhost:5433`) y `ferrebot-redis` (Redis, `localhost:6379`). Tras un
reinicio del PC, Redis levanta solo (`--restart unless-stopped`); **Postgres necesita el `docker
start` manual**.

## 2. Sembrar la clínica demo (una vez; es idempotente)

```powershell
.venv\Scripts\python.exe -m tools.seed_clinica_demo
```

Crea/asegura el tenant `clinica-demo` (BD `ferrebot_clinica-demo`, migrada), siembra 2 profesionales,
3 servicios, disponibilidad L–V y las reglas, y enciende los flags `pack_agenda` + `canal_whatsapp`.
Re-ejecutarlo no duplica nada.

## 3. Arrancar el API (FastAPI / uvicorn) — puerto 8000

Desde la **raíz del repo** (lee el `.env` local):

```powershell
.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Verifica que responde: abre <http://localhost:8000/health> → `{"status":"ok"}`.

## 4. Arrancar el dashboard (Vite) — puerto 5173

El dashboard llama a `/api/v1` y Vite lo **proxya** a `http://localhost:8000` (ver
`dashboard/vite.config.js`, sin reescritura) — **no** hace falta `VITE_API_BASE`. Solo hay que
decirle qué empresa es en dev, vía el header `X-Tenant-Slug` que arma `lib/api.js` desde
`VITE_TENANT_SLUG`.

Crea **`dashboard/.env.local`** con:

```
VITE_TENANT_SLUG=clinica-demo
```

Luego:

```powershell
cd dashboard
npm run dev          # Vite en http://localhost:5173
```

## 5. Entrar al dashboard sin Telegram (helper de dev)

```powershell
.venv\Scripts\python.exe -m tools.dev_token        # default: clinica-demo
```

Imprime el JWT de admin y **exactamente** qué poner en `localStorage` (claves de `lib/api.js`:
`ferrebot_token` y el objeto usuario `ferrebot_user`, con la forma que guarda `hooks/useAuth.js`).

Pasos en el navegador:

1. Abre <http://localhost:5173> (te manda a `/login`).
2. Abre la **consola** del navegador (F12 → Console) y **pega la línea única** que imprimió el
   comando (hace los dos `localStorage.setItem` y navega a `/agenda`). Forma:

   ```js
   localStorage.setItem('ferrebot_token','<JWT>');
   localStorage.setItem('ferrebot_user','{"id":1,"rol":"admin","tenant":"clinica-demo"}');
   location.href='/agenda';
   ```

El token expira en 12 h; si caduca, vuelve a correr `tools.dev_token` y repite. (El SPA, ya
logueado, trae `GET /api/v1/config`: tematiza y trae las features — `pack_agenda` viene activo, así
que la pestaña **Agenda** aparece en el menú.)

## 6. Verificar el pack Agenda

En la pestaña **Agenda** (sub-tab **Citas**, vista calendario del día):

- **Columnas por recurso**: deben verse **Dra. García** y **Lic. Martínez** como columnas, con la
  rejilla de horas (07:00–21:00, hora Colombia).
- **Filtros**: navegación de fecha (‹ Hoy / día ›), Estado y Profesional/Recurso.
- **Alta manual**: botón **“Nueva cita”** → elige servicio (Limpieza dental / Blanqueamiento /
  Consulta), recurso, fecha/hora (dentro de L–V 08–12 ó 14–18), nombre y teléfono → **Agendar**. El
  bloque aparece en la columna del recurso, coloreado por estado.
- **Acción requerida**: como `modo_confirmacion=manual`, la cita nueva entra **pendiente** y aparece
  en el panel lateral con **Aprobar** / **Rechazar**.
- **Tiempo real**: al crear/aprobar/cancelar, la grilla y el panel se actualizan en vivo (SSE por el
  mismo proxy `/api/v1`).

En el sub-tab **Configuración** (visible porque el token es de admin) puedes revisar/editar
servicios, recursos, asignaciones, disponibilidad, bloqueos y las reglas.

---

### Notas

- Para ver el flujo **end-to-end con el agente de WhatsApp** (que las citas entren solas) hace falta,
  además, el worker ARQ y mapear el número de Kapso:
  `python -m tools.seed_wa_numero <phone_number_id> clinica-demo` (ver `docs/whatsapp-agentes-arquitectura.md`).
  Para verificar solo el dashboard, el alta manual basta.
- Si el dashboard muestra 404/empresa no encontrada: revisa que `VITE_TENANT_SLUG=clinica-demo` esté
  en `dashboard/.env.local` y **reinicia `npm run dev`** (Vite lee las env al arrancar).
- Si `/health` no responde: el API no está arriba o el `.env` no apunta al `localhost:5433` correcto.
