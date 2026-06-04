# Seguridad

## Secretos

- **Nunca** en el código ni en git. Solo `.env` local (ignorado) para variables de plataforma.
- **Credenciales por empresa** (MATIAS, Cloudinary, token de Telegram, resolución DIAN) se guardan **cifradas en reposo** en el control DB (tabla `secretos_empresa`), usando `SECRETS_MASTER_KEY`. Se descifran solo en memoria por request.
- Rotación: planificar rotación de `SECRETS_MASTER_KEY` y de los secretos por empresa. Evaluar un gestor (Doppler / Infisical / Railway secrets / Vault).

## Aislamiento entre empresas

- Una base de datos por empresa. Ninguna consulta puede cruzar tenants (la conexión ya apunta a la base correcta). Ver `.claude/rules/multitenancy.md`.
- Backups y restauración **por empresa** (ver `docs/runbook.md`).

## Retención (DIAN)

- Los documentos electrónicos (facturas, DS, notas, eventos) deben conservarse por el periodo legal (~5 años). No borrar histórico fiscal en limpiezas de datos.

## Datos personales (Colombia) — pendiente

- **Habeas Data (Ley 1581):** cuando haya empresas-cliente reales, definir política de tratamiento de datos, consentimiento y export/borrado por solicitud. Anotado como tarea futura (hoy sin empresas externas).

## Reporte de vulnerabilidades

- Reportar en privado a los mantenedores antes de divulgar.
