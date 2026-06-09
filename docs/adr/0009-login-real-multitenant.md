# ADR 0009 — Login real (email/contraseña) multi-tenant en link compartido

> Estado: **Propuesto** (8 jun 2026). Reemplaza el acceso por `dev_token` por un login real que un
> cliente pueda usar. Fase A1 del `docs/roadmap-superficies-web.md`. Es **greenfield**: hoy no existe
> infraestructura de contraseñas.

## Contexto

Hoy el dashboard se entra de dos formas, ninguna entregable a un cliente:
- **`dev_token`** (`tools/dev_token.py`): genera un JWT y se pega en la consola del navegador. Solo dev.
- **Telegram Login Widget** (`modules/auth/`): el login "real" existente, pero atado a tener un bot de
  Telegram por empresa y al `telegram_id` del usuario. No sirve para una clínica que no usa Telegram.

Dos hechos del código que mandan el diseño:
1. **El resolver de tenant** (`core/tenancy/resolver.py`) resuelve en orden: **subdominio → header
   `X-Tenant-Slug` → claim `tenant` del JWT → default**. O sea, un JWT con el claim `tenant` **ya** basta
   para que cada request apunte a la base correcta (es como funciona el `dev_token`).
2. **Los `usuarios` viven en la base de CADA tenant** (tabla creada en `migrations/tenant/`, leída por
   `SqlUsuariosBotRepo` sobre la sesión del tenant). No hay directorio global de usuarios.

El problema del **link compartido**: cuando el cliente abre `app.tudominio.com` y va a loguear, **todavía
no hay tenant resuelto** (no hay subdominio). Pero los usuarios están repartidos por base de tenant. Hay
que poder responder "¿de qué empresa es este email?" **antes** de tocar ninguna base de tenant.

## Decisión

**Login email/contraseña sobre un link compartido, resolviendo el tenant DESDE el usuario** mediante un
**directorio de identidades en el control DB**.

### D1 — Directorio global de identidades en el control DB

Nueva tabla en el **control DB** (no en el tenant), p. ej. `identidades`:

| Campo | Para qué |
|---|---|
| `email` (único, ciudadano de primera clase) | con qué entra el usuario |
| `password_hash` | **Argon2id** (o bcrypt) — nunca la contraseña en claro |
| `empresa_id` (FK a `empresas`) | a qué tenant pertenece → de aquí sale el claim `tenant` |
| `usuario_id` | el id del usuario DENTRO de la base de su tenant (para el `Principal`/RBAC) |
| `rol`, `activo`, timestamps | gating y estado |

El directorio guarda **solo datos de autenticación y ruteo** (email, hash, a qué empresa va), **no** datos
de negocio — coherente con "el control DB tiene la metadata de tenancy; el negocio vive en la app DB"
(`multitenancy.md` §3). El aislamiento DB-per-tenant queda intacto.

### D2 — Flujo de login (se invierte respecto a hoy)

Hoy: el middleware resuelve el tenant **primero** (subdominio/token), luego valida. Con link compartido se
invierte:

1. `POST /auth/login {email, password}` (sin tenant aún).
2. Buscar `identidades` por `email` en el **control DB**; verificar `password_hash` en **tiempo constante**.
3. Si ok y `activo`: emitir el **JWT con claim `tenant` = slug de su empresa** (+ `usuario_id`, `rol`),
   reusando `core/auth/jwt.create_access_token`.
4. El frontend guarda el token; de ahí en adelante el **resolver lo usa** (claim `tenant`) → todo apunta a
   la base de esa empresa. `GET /config` carga su branding + sus packs.

### D3 — Infra de contraseñas (lo greenfield)

- **Hashing**: `argon2-cffi` (o `passlib[bcrypt]`). Parámetros sensatos; nunca SHA plano.
- **Endpoints**: `login`; e **invitación** (el admin de la empresa o el super-admin crea la identidad y
  manda un enlace de set-password) y **reset** (olvidé mi contraseña). El **alta de la identidad** se
  integra al **provisionador** (ADR 0007): al dar de alta un tenant, crear la identidad admin con un
  enlace de set-password — no contraseñas en el manifiesto.
- **Frontend**: pantalla de login email/contraseña en `dashboard/src/pages/Login.jsx` (reemplaza el
  widget/dev_token como entrada primaria), set-password y reset.

### D4 — Seguridad (no negociable)

- Hash fuerte + comparación en tiempo constante; **sin enumeración de usuarios** (mismo mensaje y tiempo
  para email inexistente vs. contraseña errada).
- **Rate limit** por IP/email y bloqueo temporal tras N intentos (Redis, ya disponible).
- Secretos/hashes nunca en logs. JWT con expiración corta + (futuro) refresh.
- **Habeas Data (Ley 1581)**: al haber datos personales de clientes externos, queda pendiente lo anotado
  en `security.md`.

## Consecuencias

**A favor:** un cliente real entra con email/contraseña a un link compartido y ve su dashboard, sin
`dev_token` ni Telegram. No requiere comprar dominio (funciona sobre el URL de Railway; el subdominio
queda como pulido opcional, ya soportado por el resolver). El directorio en control DB habilita además el
panel super-admin (gestionar accesos) y, después, el self-serve.

**En contra / costo:** nueva tabla + migración de control; infra de password desde cero (hash, endpoints,
pantallas); hay que **sembrar identidades** para los tenants existentes (Punto Rojo, clinica-demo) y
mantener sincronía `identidades.usuario_id` ↔ `usuarios` del tenant. Una identidad mapea a **una** empresa
(si alguien gestiona dos negocios, es caso futuro).

**Transición:** el Telegram Login Widget puede **convivir** un tiempo (entrada alterna) o retirarse; el
`dev_token` queda solo para desarrollo.

## Alternativas consideradas

- **Usuarios por-tenant + tenant conocido por subdominio** (login en `clinica.tudominio.com`, sin
  directorio global). Evita la tabla nueva pero **exige dominio propio + subdominio por empresa** desde el
  día 1, justo lo que querías evitar por costo. Puede coexistir luego para clientes con marca propia.
- **Pedir el "slug de empresa" en el formulario de login** (además de email/clave) para resolver el tenant
  sin directorio global. Rechazado: peor UX y frágil (el cliente no conoce su slug).
- **Federar identidad con un IdP (Google/OAuth)**. Aplazado: útil después; no cambia el diseño del
  directorio (el `email` sigue siendo la llave).

## Decisión abierta

- Nombre/forma exacta del directorio (`identidades` vs extender una tabla existente del control).
- ¿`usuario_id` por-tenant materializado en el directorio, o resolverlo en el login contra la base del
  tenant tras conocer `empresa_id`? (Lo segundo evita desincronización; cuesta una consulta extra.)
