# Autenticación y RBAC

> Cómo se autentica cada actor y qué puede hacer. Endpoints en `api-contract.md`; aislamiento en `tenancy.md`.

## Roles

`super_admin` (operador del SaaS, global) > `admin` (de una empresa) > `vendedor` (de una empresa). `cajero`/`supervisor` quedan como expansión del enum `usuario_rol`.

## JWT

- Firmado HS256 con `SECRET_KEY`. Claims: `{ sub: usuario_id, tenant_id, rol, iat, exp }`. El `super_admin` lleva `tenant_id: null`.
- **Access token** corto (p. ej. 30–60 min) + **refresh token** (días) para renovar sin re-login.
- **Regla de aislamiento:** el `tenant_id` del JWT debe coincidir con la empresa resuelta por subdominio. Si no coincide → 403. Nunca confiar solo en el JWT para elegir empresa.

## Flujos de login

- **Dashboard (empresa):** Telegram Login Widget → `POST /auth/telegram` valida la firma de Telegram, busca `usuarios.telegram_id` en la app DB de la empresa, emite JWT.
- **Bot (empresa):** decorador `@protegido` autentica por `chat_id` contra `usuarios.telegram_id` de esa empresa + rate limiting. Un `chat_id` no registrado no opera.
- **Super-admin (plataforma):** email + password (hash) contra `super_admins` del control DB.

## Dependencias (FastAPI)

```python
get_current_user      # valida JWT, retorna {usuario_id, tenant_id, rol}; verifica tenant
require_role("admin") # 403 si el rol es insuficiente
get_filtro_usuario    # vendedor -> su usuario_id (ve solo lo suyo); admin -> None (ve todo)
get_filtro_efectivo   # admin puede impersonar un vendedor con ?vendor_id=N
```

## Matriz de permisos

| Capacidad | super_admin | admin | vendedor |
|---|---|---|---|
| Gestionar empresas, planes, features | Sí | No | No |
| Cargar secretos/branding de empresa | Sí | No | No |
| Ver datos de su empresa | — | Todos | Solo los suyos |
| CRUD usuarios de la empresa | — | Sí | No |
| CRUD productos / precios | — | Sí | No |
| Registrar ventas / caja / gastos | — | Sí | Sí |
| Anular venta | — | Sí | No |
| Emitir factura / DS / notas | — | Sí | Sí (si feature on) |
| Reportes financieros (resultados) | — | Sí | No |
| Selector de vendedor (`?vendor_id`) | — | Sí | No |

## Alcance de datos del vendedor

- El `vendedor` solo ve **sus** ventas, caja y gastos (filtro por `vendedor_id` vía `get_filtro_usuario`).
- El `admin` ve todo y puede ver lo de un vendedor con `?vendor_id=N` (`get_filtro_efectivo`).
- `multi_vendedor` (feature): si está off, hay un solo vendedor y los filtros se simplifican.

## Notas

- Toda ruta de negocio: `Depends(get_current_user)` + `require_feature(...)` cuando aplique.
- Auditoría: cada mutación guarda `usuario_id` y fecha en la app DB de la empresa.
