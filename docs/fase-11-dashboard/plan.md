# Fase 11 — Dashboard web (MVP white-label) · plan + prompts

> El mayor bloque individual. **Greenfield** en el repo SaaS (no hay frontend), pero el **FerreBot original
> tiene un dashboard React completo y moderno** → la estrategia es **portar-y-adaptar**, no construir de cero.
> Cowork redacta; Andrés ejecuta en Claude Code.

## Qué es este dashboard (alcance conceptual)

El **dashboard operativo white-label por empresa**: la app React que usa el personal de la ferretería
(admin/vendedor) para operar la tienda desde la web. **Una sola app** servida a todos los tenants,
tematizada y con tabs gateados por `GET /config`. Punto Rojo lo usa como tenant #1; es el mismo dashboard
para cualquier cliente del SaaS. **NO** es el panel super-admin del operador SaaS (gestión de empresas/planes/
billing) — eso es **Fase 13**.

## Decisiones (cerradas con Andrés)

- **D1 — Estrategia:** **portar-y-adaptar** el dashboard del FerreBot original (React 18 + Vite 5 + Tailwind 3
  + shadcn/Radix, React Router 6, recharts, framer-motion, cmdk, sonner; `AppShell`, `useAuth`, `useRealtime`,
  16 tabs). Reusa UI probada; el trabajo real es **recablear la capa de datos** al contrato multi-tenant.
- **D2 — Alcance MVP:** **núcleo completo** = shell + tabs núcleo, incluyendo los **endpoints backend que
  faltan**. Diferir tabs fiscales (Facturación, Libro IVA, Compras fiscal, Proveedores, FE recibidas, Kardex) y
  reportes pesados (Resultados, Top productos) a la **Fase 12**, junto a su backend.

## Flujo auth + tenant (ya soportado por el backend)

- **Resolución de tenant** (`core/tenancy/resolver.py`): subdominio `slug.BASE_DOMAIN` → header
  `X-Tenant-Slug` (local) → claim `tenant` del JWT. `TenantMiddleware` lo aplica a todo request no-público.
- **Producción:** dashboard servido en `puntorojo.BASE_DOMAIN` → el subdominio resuelve el tenant (white-label
  por dominio/branding). **Local:** el SPA manda `X-Tenant-Slug`.
- **JWT** (`core/auth/jwt.py`): claims `sub`(user_id), `tenant`(slug), `rol`. `get_current_user` exige que el
  claim `tenant` coincida con el tenant resuelto (aislamiento).
- **Login (FALTA en el backend):** no hay `POST /auth/login`. Debe verificar el **Telegram Login Widget** con
  el **token del bot de esa empresa** (cifrado en `secretos_empresa`, control DB), mapear `telegram_id` →
  `usuarios` (tenant DB) → `rol`, y emitir el JWT.

## Huecos de backend del MVP (4)

| # | Endpoint | Para |
|---|---|---|
| B1 | `POST /api/v1/auth/login` (+ `GET /auth/me`) | Login Telegram → JWT con tenant. Foundational. |
| B2 | `modules/clientes/router.py`: GET `/clientes`, POST `/clientes`, GET `/clientes/{id}` | Tab Clientes |
| B3 | GET `/ventas` con filtros de fecha (lista/historial) | Tab Historial |
| B4 | GET `/reportes/resumen` (KPIs del día) | Tab Hoy |

## Frontend (portar-y-adaptar)

Reusar la UI del original; **reescribir la capa de datos** de cada pieza a los endpoints SaaS (`/api/v1`,
shapes nuevos). "Portar un tab" = conservar el JSX/UX, recablear sus `fetch` al contrato SaaS.

## Desglose

| E | Tipo | Entregable |
|---|---|---|
| E1 | back | `POST /auth/login` (verificación Telegram con bot token por empresa) + `GET /auth/me` + JWT. pytest. |
| E2 | back | Endpoints núcleo que faltan: clientes (B2), lista de ventas/historial (B3), resumen del día (B4). pytest. |
| E3 | front | **Andamiaje**: copiar `dashboard/` (Vite+Tailwind+ui/+AppShell/Sidebar/Header/MobileNav/routes), servido por FastAPI (`StaticFiles`/`dashboard/dist`); boot con `GET /config` → tema desde branding (`color_primario`) + feature-gating de tabs. |
| E4 | front | **Auth**: portar `Login` (Telegram Login Widget) + `useAuth` + `authFetch` (Bearer + `X-Tenant-Slug`); `ProtectedRoute`; cablear a `/auth/login`. |
| E5 | front | **Tiempo real**: portar `useRealtime` → `/api/v1/events` (SSE); en `reconnected`, re-fetch. |
| E6 | front | **Tabs núcleo**: portar y recablear Hoy, Ventas rápidas, Inventario, Caja, Gastos, Clientes, Historial a los endpoints SaaS. |
| E7 | verif | Build `dashboard/dist` servido por la API; smoke E2E (login → Hoy → registrar venta → ver update SSE). |

**Criterio de cierre:** Punto Rojo puede entrar (tema Punto Rojo), ver el día, vender, inventario, caja,
gastos, clientes y el historial desde la web; tabs gateados por `/config`; updates en vivo por SSE.

## Testing del frontend (cómo es "GREEN" aquí)

El ciclo TDD del repo es pytest (backend). En frontend:
- **Backend (E1-E2):** pytest como siempre (RED→GREEN).
- **Frontend (E3-E6):** **Vitest + React Testing Library** para lógica/cableado clave (useAuth, authFetch con
  header de tenant, feature-gating desde /config, parseo de respuestas). No perseguir 100% de UI.
- **E7:** smoke E2E (manual guiado o Playwright si se quiere) del flujo crítico. Definir al llegar.

> **Decisión menor (recomiendo seguir el original):** JavaScript + JSX (no TypeScript), para maximizar el
> reuso directo del dashboard original sin reescritura de tipos. Mismas libs/verslones del original.

---

## E1 — `POST /auth/login` (prompt RED para Claude Code)

```
Contexto: FerreBot SaaS. Falta el login del dashboard. No existe POST /auth/login; sí existe
core/auth/jwt.py::create_access_token(user_id, tenant, rol) y la resolución de tenant (TenantMiddleware →
request.state.tenant). El dashboard usará el Telegram Login Widget: el frontend recibe de Telegram un payload
firmado (id, first_name, username, auth_date, hash) y lo manda al backend; el backend debe VERIFICAR el hash
con el token del bot DE ESA EMPRESA (secreto cifrado en secretos_empresa, control DB), mapear telegram_id →
usuarios (tenant DB) → rol, y emitir el JWT.

Revisa docs/auth-rbac.md y docs/secrets.md por el patrón de secretos por empresa. El bot del SaaS ya resuelve
telegram_id → usuario (apps/bot): reusa esa lógica de mapeo (auth/usuarios o el repo equivalente), no la
reimplementes.

TDD:
1) RED — tests/test_auth_login.py (patrón test_facturacion_router: app mínima + ASGITransport +
   dependency_overrides):
   - verificación de hash del Telegram Login Widget (función PURA): payload válido con un bot token dado → ok;
     hash manipulado → falla. (HMAC-SHA256 con clave = SHA256(bot_token), por el spec de Telegram.)
   - POST /api/v1/auth/login con payload válido + telegram_id que mapea a un usuario admin de la empresa →
     200 con { token, usuario:{id,rol,tenant} }; el token decodifica a claims sub/tenant/rol correctos.
   - telegram_id sin usuario en la empresa → 401/403.
   - hash inválido → 401.
2) GREEN —
   - modules/auth/router.py: POST /auth/login (deps inyectables: lectura del bot token por empresa desde
     secretos_empresa; mapeo telegram_id→usuario por la sesión del tenant). GET /auth/me (devuelve el Principal
     del token). Verificación del widget en una función pura testeable.
   - monta el router en apps/api/main.py (prefix /api/v1). NO feature-gate (es auth).

Reglas: async/await, type hints 3.10+, docstrings español, sin print. Secretos SOLO desde secretos_empresa
(cifrados); nunca hardcode. Acceso a datos por repos/sesión del tenant. NO cambies create_access_token.
Corre: .venv/Scripts/python.exe -m pytest tests/test_auth_login.py -q
```

**Qué reviso:** verificación del widget correcta (HMAC con SHA256(bot_token)), bot token leído cifrado por
empresa (nunca global/hardcode), mapeo telegram_id→usuario por la sesión del tenant (aislamiento), y JWT con
los claims correctos.

> E2-E7 los entrego uno a uno al avanzar. E1 (login) primero porque el shell depende de él.
