# Seguridad (resumen para desarrollo)

Detalle completo en `/SECURITY.md`.

- Secretos nunca en código ni en git. Plataforma en `.env`; por empresa **cifrados** en control DB con `SECRETS_MASTER_KEY`.
- Auth: JWT en la API (`core/auth`), `@protegido` en el bot. Roles: `super_admin` (operador SaaS) > `admin` (empresa) > `vendedor`.
- Validar toda entrada (Pydantic). Acceso a datos solo por repositorios (evita inyección).
- No exponer rutas internas ni IDs de otras empresas.
- Histórico fiscal DIAN: conservar ~5 años, no borrar en limpiezas.
- Habeas Data (Ley 1581): pendiente hasta tener empresas externas.
