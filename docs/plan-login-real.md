# Plan — Login real multi-tenant (Fase A1, prompts para Claude Code)

> Acompaña al **ADR 0009** (`docs/adr/0009-login-real-multitenant.md`) y al roadmap
> (`docs/roadmap-superficies-web.md`, Fase A1). Reemplaza el acceso por `dev_token`/Telegram por login
> email+contraseña sobre link compartido.
>
> **Cómo se usa:** una fase a la vez. Andrés pega el prompt, Claude Code lo implementa (TDD), Andrés
> revisa el diff con Cowork, CI verde, merge. **Prerrequisito: A2 (PR #2) mezclado** — estas fases
> construyen sobre `main` ya con la separación de packs.

## Decisiones ya tomadas (cierran las abiertas del ADR 0009)

- **Directorio:** tabla nueva `identidades` en el **control DB**.
- **`usuario_id` MATERIALIZADO** en `identidades` (no se resuelve en cada login). La fila ES el enlace:
  `email → (empresa_id, usuario_id, rol)`. El login lee solo el control DB; no toca la base del tenant.
  El provisioning crea la fila `usuarios` del tenant y guarda su `id` en `identidades` en el mismo paso.
- **Hashing:** Argon2id (`argon2-cffi`).
- **Telegram widget:** convive como entrada alterna; `dev_token` solo dev.

## Mapa de fases

| Fase | Entrega | Depende de |
|---|---|---|
| A1.1 | Directorio `identidades` (control) + hashing + repositorio | A2 mezclado |
| A1.2 | Endpoint `POST /auth/login` (autentica → JWT) + seguridad | A1.1 |
| A1.3 | Endpoints set-password + reset (flujo por token) | A1.2 |
| A1.4 | Integración con el provisionador (manifiesto `admin.email`) + grandfather de tenants existentes | A1.3 |
| A1.5 | Frontend: pantalla de login email/contraseña + set-password/reset | A1.2 (API lista) |

Reglas transversales (`.claude/rules/`): TDD; secretos/hashes jamás en logs ni en claro; control DB
aparte del tenant; idempotencia donde aplique; logging estructurado con `tenant_id`/`request_id`.

---

## Fase A1.1 — Directorio de identidades + hashing

```
Contexto: implementamos la Fase A1.1 del docs/plan-login-real.md, según docs/adr/0009-login-real-multitenant.md.
Lee ambos + .claude/rules/. Hechos del código: el control DB tiene `empresas` (id, slug, ...) y los
`usuarios` viven en la base de CADA tenant (migrations/tenant, key telegram_id). El resolver ya usa el
claim `tenant` del JWT (core/tenancy/resolver.py). create_access_token(user_id, tenant, rol) en core/auth/jwt.

Tarea (solo fundamento, SIN endpoints):
1) Migración de CONTROL (migrations/control): tabla `identidades`:
   - email (TEXT, único, normalizado a minúsculas — usa citext o normaliza en la capa),
   - password_hash (TEXT, nullable: una identidad puede existir sin contraseña aún, pendiente de set-password),
   - empresa_id (FK a empresas, ON DELETE CASCADE),
   - usuario_id (BIGINT: el id del usuario DENTRO de la base de su tenant; materializado),
   - rol (TEXT), activo (BOOL default true), creado_en/actualizado_en.
   Índice único por email. Idempotente con el resto del árbol de control.
2) Módulo de hashing core/auth/passwords.py con argon2-cffi (añade argon2-cffi a pyproject):
   hash_password(plano)->str y verify_password(plano, hash)->bool. PURO, sin IO. verify NUNCA lanza
   (hash inválido/None -> False). Parámetros por defecto sensatos de argon2id.
3) Repositorio core/tenancy/identidades_repo.py (control DB, driver acorde al resto del control):
   buscar_por_email(email)->Identidad|None, crear/upsert(email, empresa_id, usuario_id, rol),
   set_password_hash(identidad_id, hash), normalizando email a minúsculas SIEMPRE.

TDD: hash roundtrip (verify ok), verify rechaza clave mala y maneja hash None/corrupto sin lanzar; repo
upsert + lookup por email case-insensitive. Corre pytest. No commitees aún.
```

**Verificación A1.1:** migración corre limpio (upgrade/downgrade); hashing roundtrip verde; lookup por
email case-insensitive. Nada de endpoints todavía.

---

## Fase A1.2 — Endpoint de login (autenticar → resolver tenant → JWT)

```
Contexto: Fase A1.2 del docs/plan-login-real.md. Existe `identidades` + hashing + repo (A1.1). El login se
INVIERTE respecto al actual (que resuelve el tenant antes): ahora autenticamos primero y el tenant sale
del usuario. NO rompas el login por Telegram widget existente (modules/auth) — convive.

Tarea: POST /auth/login con body {email, password} (SIN tenant en el contexto):
1) Busca la identidad por email en el CONTROL DB (repo A1.1); verifica password con verify_password
   (tiempo constante, sin ramificar por "email no existe" vs "clave mala").
2) Si ok y activo: emite el JWT reusando create_access_token(user_id=identidad.usuario_id,
   tenant=slug_de_su_empresa, rol=identidad.rol). Devuelve {token, usuario:{id, rol, tenant}} con la misma
   forma que el login actual (LoginOut).
3) SIN enumeración de usuarios: mismo status (401) y mensaje genérico y tiempo similar para email
   inexistente, clave errada e identidad inactiva (hashea un dummy cuando el email no existe para igualar
   el costo temporal).
4) Rate limit + lockout por email/IP en Redis (ya disponible): N intentos fallidos -> bloqueo temporal,
   respuesta 429. Configurable.

TDD: login correcto -> 200 + JWT con el claim tenant correcto; clave mala -> 401 genérico; email
inexistente -> 401 idéntico (sin filtrar que no existe); inactivo -> 401; tras N fallos -> 429. Inyecta el
repo/redis para testear sin red. Corre pytest. No commitees aún.
```

**Verificación A1.2:** un login con email+clave de una identidad sembrada a mano emite un JWT cuyo claim
`tenant` apunta a su empresa; email inexistente y clave mala dan la **misma** respuesta; lockout funciona.

---

## Fase A1.3 — Set-password + reset (flujo por token)

```
Contexto: Fase A1.3 del docs/plan-login-real.md. Existe el login (A1.2). Las identidades se crean SIN
contraseña (password_hash NULL); el usuario la establece por un enlace con token.

Tarea:
1) Tokens de un solo uso, con expiración, para set-password y reset (firmados o guardados en control DB/
   Redis con hash; nunca el token en claro en BD). Liga el token a una identidad.
2) POST /auth/set-password {token, password}: valida el token (no expirado, no usado), aplica
   hash_password y lo guarda (set_password_hash), invalida el token. Política mínima de contraseña.
3) POST /auth/reset/solicitar {email}: si existe, genera token y (por ahora) lo DEVUELVE/loguea para que
   Andrés lo entregue manual (el envío de email real es un TODO aparte; no lo bloquees). SIN enumeración:
   responde 200 genérico exista o no el email.
4) POST /auth/reset/confirmar {token, password}: igual que set-password.

TDD: set-password con token válido -> permite luego login; token expirado/usado -> error; reset genera
token y permite cambiar la clave; solicitar reset de email inexistente -> 200 genérico (sin enumerar).
Corre pytest. No commitees aún.
```

**Verificación A1.3:** una identidad sin clave queda usable tras set-password; el token no se puede reusar;
reset no filtra si el email existe.

---

## Fase A1.4 — Integración con el provisionador + grandfather

```
Contexto: Fase A1.4 del docs/plan-login-real.md. Existen login + set-password (A1.2/A1.3). Hay que crear la
identidad del admin al dar de alta un tenant, y sembrar identidades para los tenants YA existentes.

Tarea:
1) Manifiesto: añade `admin.email` (opcional pero recomendado) a tools/manifest/schema.py (+ validación si
   aplica). NUNCA una contraseña en el manifiesto.
2) Provisionador (tools/provision_from_manifest / provision_tenant_full): tras crear el usuario admin en la
   base del tenant (ya ocurre en _seed), captura su `usuario_id` y crea la `identidad` en el control DB
   (email del manifiesto, empresa_id, usuario_id, rol=admin, password_hash NULL) + genera un token de
   set-password y LO IMPRIME/loguea para que Andrés se lo pase al cliente. Idempotente (re-provisionar no
   duplica la identidad).
3) Grandfather: un comando/migración que siembre identidades para los tenants existentes (puntorojo,
   clinica-demo) a partir del admin que ya tienen en su base del tenant — pidiendo/recibiendo el email del
   admin como parámetro (no lo inventes). Emite el token de set-password para cada uno. Idempotente.

TDD: provisionar desde un manifiesto con admin.email crea exactamente una identidad ligada al usuario del
tenant; re-provisionar no duplica; el grandfather siembra la identidad de un tenant existente sin tocar su
data de negocio. Corre pytest. No commitees aún.
```

**Verificación A1.4:** dar de alta un tenant nuevo deja su admin listo para set-password; Punto Rojo y
clinica-demo quedan con identidad sembrada (con su token de set-password) sin tocar su negocio.

---

## Fase A1.5 — Frontend: pantalla de login

```
Contexto: Fase A1.5 del docs/plan-login-real.md. La API de login/set-password/reset ya existe (A1.2/A1.3).
Hoy dashboard/src/pages/Login.jsx usa Telegram widget / dev_token. Hazlo email+contraseña como entrada
PRIMARIA, sobre el link compartido (sin subdominio).

Tarea (React, sigue el estilo del dashboard y components/ui):
1) Login.jsx: formulario email + contraseña -> POST /auth/login -> guarda el token (como hoy) -> carga
   GET /config -> entra al shell tematizado. Estados de error claros (credenciales inválidas, bloqueado).
   Usa design:ux-copy para los textos (mensajes de error, vacíos) y mantén la consistencia visual.
2) Pantallas set-password (desde el enlace con token) y "olvidé mi contraseña" (solicitar reset).
3) Deja el dev_token solo para desarrollo; el Telegram widget como opción alterna si ya existía (no lo
   rompas).

TDD (vitest, patrón de dashboard/src): el form llama a /auth/login y redirige al éxito; muestra error en
401/429; set-password con token llama al endpoint correcto. Corre vitest + el pytest backend completo. No
commitees aún.
```

**Verificación A1.5:** entrar a `app.<host>` muestra login email/contraseña; con credenciales válidas el
cliente cae en su dashboard tematizado por sus packs; errores claros. **Cierra la Fase A1.**

---

## Cierre de A1

Con A1 + A2 hechos: un cliente real entra a un link compartido con email/contraseña y ve **su** dashboard
limpio (solo sus packs). Sigue la **Fase B (panel super-admin)** — que reusa este mismo provisionador +
estas identidades para gestionar accesos.

## Checklist

- [ ] A1.1 — `identidades` + hashing + repo.
- [ ] A1.2 — login (autentica→JWT, sin enumeración, lockout).
- [ ] A1.3 — set-password + reset por token.
- [ ] A1.4 — identidad en el provisionador + grandfather (puntorojo, clinica-demo).
- [ ] A1.5 — frontend de login.
