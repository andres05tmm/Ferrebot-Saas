# Fase 7 — Consolidación y guardarraíles · plan + prompts

> Plan re-scopeado contra el código real de la rama `feat/fase-6-fact`. Cowork (copiloto) redacta;
> Andrés ejecuta en Claude Code. Ciclo: **RED (tests + esqueletos) → Cowork revisa → GREEN → commit**.

## Re-scope (lo que ya estaba hecho)

- `core/db/session.py::get_tenant_db` **ya** está anotado `request: Request` (el fix de Fase 6 aterrizó).
  Los smokes E2 pasan de "destapar el bug" a **blindarlo** (regresión).
- `apps/api/main.py` **ya** define `/health` y `/ready`, pero son *stubs* estáticos. E3 = darle contenido
  real a `/ready`.

## Entregables y orden

1. **E1 — Merge a `main`** (Andrés; git no corre desde Cowork). Checkpoint abajo.
2. **E2 — Smokes HTTP** de ventas/caja/inventario/fiados (guardarraíl; deben pasar).
3. **E3 — `/ready` real** (control DB + Redis; 503 si falla).
4. **E4 — Caché de `MatiasClient` por tenant** en el worker.

E2/E3/E4 sobre una rama nueva `feat/fase-7-consolidacion` salida de `main` ya actualizado. Si prefieres no
mergear todavía, los prompts funcionan igual sobre `feat/fase-6-fact`; solo cambia de dónde sale la rama.

**Criterio de cierre de fase:** `main` con suite verde + smokes HTTP de los 4 routers núcleo + `/ready`
comprobando dependencias + caché de cliente con test. Correr `.venv/Scripts/python.exe -m pytest`.

---

## E1 — Checkpoint de merge (recomendación)

Dos ramas grandes sin mergear: `feat/fase-5-bot` (@ `25bb3ef`) y `feat/fase-6-fact` (@ `c47f1ce`).

**Recomendación de orden:** mergear **fase-5 primero**, luego **fase-6**. Razón: fase-6 (facturación +
worker ARQ + RC wiring del lifespan) se construyó después y es más probable que asuma piezas de fase-5
(bot/eventos) que al revés. Hacerlo al revés multiplica conflictos en `apps/` y `core/`.

**Procedimiento sugerido (lo corres tú):**

```bash
git checkout main
git pull
git merge --no-ff feat/fase-5-bot      # resolver conflictos, suite verde
.venv/Scripts/python.exe -m pytest
git merge --no-ff feat/fase-6-fact      # resolver conflictos, suite verde
.venv/Scripts/python.exe -m pytest
```

**Qué reviso yo (Cowork) tras cada merge:** me pasas `git diff main~1...main` (o el diff del merge) y
verifico: que no se perdió el fix de `get_tenant_db`, que el lifespan del API conserva el `arq_pool`, que
`/health`/`/ready` siguen montados, y que no quedó SQL suelto fuera de repos. **Zona de conflicto probable:**
`apps/api/main.py` (lifespan + routers), `core/events/`, `tests/conftest.py`.

Si el merge se complica, paramos y lo miramos juntos antes de forzar.

---

## E2 — Smokes HTTP de routers núcleo (prompt para Claude Code)

> Pega esto en Claude Code. Es un guardarraíl: los tests deben **pasar** contra el código actual. Si alguno
> falla, destapó un bug real de wiring (repórtalo, no lo "ajustes" para que pase).

```
Contexto: FerreBot SaaS. Quiero un guardarraíl de regresión: smokes HTTP que golpeen por HTTP los routers
núcleo (ventas, inventario, caja, fiados) para blindar el wiring de dependencias —en particular que
get_tenant_db NO se trate como query param (el bug que destapó el smoke E2E de facturación)—.

Crea tests/test_smoke_routers_http.py siguiendo EXACTO el patrón de tests/test_e2e_facturacion.py y
tests/test_facturacion_router.py:
- httpx.AsyncClient sobre ASGITransport(app=app, raise_app_exceptions=False), base_url "http://t".
- App FastAPI mínima por test: FastAPI(), app.include_router(<router>, prefix="/api/v1").
- Override de auth: app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr",
  rol="admin"). (rol admin satisface todos los require_role, incluido el ajuste de inventario.)
- Override de sesión: app.dependency_overrides[get_tenant_db] con una corrutina que abra una AsyncSession
  sobre el engine de una base efímera y la ceda (yield). Usa las fixtures de tests/conftest.py:
  `tenant` (TenantDB con .engine, ya migrada a head) y `seed_producto`.
  Ejemplo del override:
      async def _db():
          async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
              yield s
      app.dependency_overrides[get_tenant_db] = _db

Casos (uno por router, todos async, deben dar 2xx):
1. inventario: sembrar con seed_producto, GET /api/v1/productos → 200 y lista no vacía.
2. caja:      GET /api/v1/caja/actual → 200 (o el código de "sin caja abierta" que el handler defina;
              afírmalo explícito, no 422). Lo importante: NO 422 por query param faltante.
3. fiados:    GET /api/v1/fiados/deudas → 200 y lista (vacía está bien).
4. ventas:    POST /api/v1/ventas con un detalle del producto sembrado (reusa seed_producto) → 201.
              Mira modules/ventas/schemas.py (VentaCrear) para el body exacto.

Reglas del repo: async/await, type hints 3.10+, sin print, Decimal en dinero, hora Colombia. No toques
código de producción salvo que un smoke revele un bug real; si lo revela, para y descríbelo.
Corre: .venv/Scripts/python.exe -m pytest tests/test_smoke_routers_http.py -q
```

**Qué reviso yo:** que use `get_tenant_db`/`get_current_user` reales como llaves del override (no parchear
con un router falso), que las afirmaciones sean explícitas (status concreto, no `< 500`), y que ningún test
modifique stock/caja sin pasar por el endpoint.

---

## E3 — `/ready` con comprobación real (prompt para Claude Code)

```
Contexto: FerreBot SaaS, apps/api/main.py. Hoy GET /ready devuelve {"status":"ready"} estático. Quiero que
compruebe las dependencias y devuelva 503 si alguna está caída. /health queda como liveness (estático ok).

TDD:
1) RED — crea tests/test_health_ready.py con ASGITransport(raise_app_exceptions=False):
   - GET /health → 200 {"status":"ok"} siempre (no toca dependencias).
   - GET /ready con control DB + Redis OK → 200, body con el estado de cada dependencia
     (p.ej. {"status":"ready","checks":{"control_db":"ok","redis":"ok"}}).
   - GET /ready con una dependencia caída → 503. Simula la caída con dependency_overrides o
     monkeypatch de la función de chequeo (no tumbes Postgres/Redis de verdad en el test).
2) GREEN — implementa el chequeo:
   - control DB: un SELECT 1 con la sesión de control (reusa core/db/session.py: _control()/get_control_db;
     NO abras un engine nuevo).
   - Redis: ping sobre el pool ARQ ya creado en el lifespan (app.state.arq_pool) o un ping perezoso; no
     crees una conexión nueva por request si puedes reusar la existente.
   - Estructura el chequeo en una función testeable (que el test pueda forzar a fallar). 503 vía
     JSONResponse(status_code=503, ...) o HTTPException(503). No bloquees el event loop (todo async).

Reglas: async/await, sin print, get_logger para el fallo, type hints 3.10+, funciones <50 líneas.
Corre: .venv/Scripts/python.exe -m pytest tests/test_health_ready.py -q
```

**Qué reviso yo:** que `/ready` reuse el pool ARQ y la sesión de control existentes (sin abrir conexiones
nuevas por request, regla de PgBouncer/pool), que el fallo se loguee estructurado, y que `/health` no toque
ninguna dependencia (liveness puro).

---

## E4 — Caché de `MatiasClient` por tenant en el worker (prompt para Claude Code)

```
Contexto: FerreBot SaaS, apps/worker/main.py. Hoy _ServicioEmision.emitir construye un MatiasClient(cred)
nuevo en CADA emisión → re-autentica (JWT) y recarga ciudades cada vez. Quiero cachear el MatiasClient por
tenant_id en el runtime del worker para reusar token y caché de ciudades entre emisiones.

TDD:
1) RED — en tests/test_worker_jobs.py (o un nuevo tests/test_worker_cache.py) agrega un test que:
   - Llame al camino de emisión dos veces para el MISMO tenant_id y verifique que el MatiasClient se
     construyó UNA sola vez (espía el constructor o inyecta una factory contadora).
   - Para tenants DISTINTOS, verifique que NO comparten cliente.
   Respeta el patrón de los tests de worker existentes (MATIAS mockeado; sin Redis real).
2) GREEN — introduce una caché tenant_id -> MatiasClient en el runtime del worker (en ctx, vía on_startup,
   o un dict a nivel de _ServicioEmision/crear_servicio). Claves:
   - El cliente httpx debe seguir siendo PEREZOSO (sin red al construir; regla del repo).
   - Concurrencia: protege la caché si dos jobs del mismo tenant entran a la vez (asyncio.Lock o
     get-or-create idempotente).
   - cred/config se resuelven por tenant; no mezcles credenciales entre empresas (aislamiento).
   - No cambies la firma pública de emitir(factura_id) ni de cargar_config_matias.

Reglas: async/await, type hints 3.10+, docstrings en español, sin print. Mantén MAX_INTENTOS y el backoff.
Corre: .venv/Scripts/python.exe -m pytest tests/test_worker_jobs.py -q
```

**Qué reviso yo:** que no se crucen credenciales entre empresas (la caché es por `tenant_id`), que el cliente
httpx siga perezoso, que la concurrencia esté cubierta, y que no cambien firmas públicas.

---

## Notas

- Tras cada GREEN: me pasas el diff (o me dices los archivos tocados) y verifico leyendo. El merge final a
  `main` lo haces tú con CI/suite en verde.
- Si E2 hace fallar algún endpoint, **es hallazgo**, no ruido: lo escalamos a `engineering:debug` antes de
  seguir.
