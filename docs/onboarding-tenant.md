# Alta de una empresa (onboarding)

1. **Registro:** el super-admin crea la empresa en el panel (nombre, NIT, slug/subdominio, plan).
2. **Base de datos:** el sistema crea la app DB de la empresa y corre `migrations/tenant` (upgrade head).
3. **Semilla:** datos base (categorías, métodos de pago, config inicial).
4. **Secretos:** cargar cifrados MATIAS (email/password/resolución/prefijo/consecutivos, DS-NO), Cloudinary y el token del bot de Telegram.
5. **Branding:** logo, color, nombre comercial, dominio.
6. **Admin:** crear el usuario administrador de la empresa.
7. **Bot:** registrar el webhook `/tg/{empresa}` con el token de su bot.
8. **Verificación:** smoke test (una venta de prueba, una emisión de factura de prueba).
9. **Suscripción:** marcar `estado = activa` (cobro manual por ahora).

> Para Punto Rojo (tenant #1), tras los pasos 1-3 se **copian** los datos desde FerreBot (ver `architecture.md` §17).

## Provisioning automatizado (operador)

`tools/provision_tenant.py` cubre los pasos 2-6 de forma **idempotente** desde un JSON de onboarding:
crea la app DB, la registra en el control DB (URL cifrada), migra, siembra el admin, y carga
**secretos cifrados** (`secretos_empresa`), **config fiscal en claro** (`config_empresa`), **branding**
y el **`telegram_id`** real del admin.

1. **Rellenar el insumo (NUNCA se commitea).** Copiar el ejemplo y poner valores REALES:
   ```bash
   cp tools/onboarding/empresa.example.json tools/onboarding/puntorojo.json
   # editar puntorojo.json: slug, nombre, nit, admin.telegram_id, secretos (telegram_token,
   # matias_email, matias_password), config (matias_*), branding (color/logo/nombre/dominio)
   ```
   `tools/onboarding/*.json` está en `.gitignore` (solo `empresa.example.json` se versiona). Los
   secretos van **cifrados** en el control DB con `SECRETS_MASTER_KEY`; nunca quedan en claro en git.

2. **Exportar el entorno** (la misma `SECRETS_MASTER_KEY` que usará la API/bot, o no se podrán descifrar):
   ```bash
   export SECRETS_MASTER_KEY=...        # IMPRESCINDIBLE que coincida con el runtime
   export CONTROL_DATABASE_URL=...      # control DB
   export ADMIN_DATABASE_URL=...        # superusuario para CREATE DATABASE
   export TENANTS_DIRECT_URL_BASE=...   # base de las app DB por empresa
   ```

3. **Provisionar:**
   ```bash
   .venv/Scripts/python.exe -m tools.provision_tenant --from tools/onboarding/puntorojo.json
   ```
   Re-ejecutar es seguro (UPSERT por `(empresa_id, clave)` / `empresa_id`): no duplica.

4. **Verificación manual (read-only; NO emite facturas):**
   - **Login real:** abrir el dashboard y entrar con el `telegram_id` configurado vía Telegram Login
     Widget → `POST /api/v1/auth/login` responde **200** y el shell carga tematizado por el branding.
   - **Catálogos MATIAS:** con `facturacion_electronica` activa, `GET /api/v1/clientes/ciudades` y
     `GET /api/v1/clientes/paises` resuelven contra **MATIAS producción** (solo lectura).
   - **La emisión real de facturas queda FUERA de este flujo, deliberada.**

5. **Bot:** registrar el webhook `/tg/{slug}` con el token cargado (`secretos_empresa.telegram_token`).
