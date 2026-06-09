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

## Dos caminos de provisioning

| Camino | Cuándo | Insumo | Comando |
|---|---|---|---|
| **Manifiesto** (ADR 0007) — *recomendado* | Tenant-agente de **servicios** (clínica, spa, beach club): packs + canal WhatsApp + datos de agenda/FAQ declarativos | un **YAML** por empresa | `python -m tools.provision_from_manifest --from <archivo>` |
| **JSON `provision_tenant`** | Lado **fiscal/POS** (ferretería): secretos MATIAS, Cloudinary, token de bot | un **JSON** por empresa | `python -m tools.provision_tenant --from <archivo>` |

Ambos son **idempotentes** y comparten la misma maquinaria de base (el manifiesto reusa
`provision_tenant_full` para crear la DB, registrar el control, migrar, admin, secretos/branding y
plan/features). El manifiesto **además** carga los datos de pack (agenda/FAQ) y mapea el número de
WhatsApp, lo que el JSON no hace.

## Provisioning declarativo desde manifiesto (ADR 0007) — recomendado

Un **manifiesto por empresa** (un archivo) + un **provisionador de un paso** que ejecuta toda la
coreografía y se puede reintentar sin romper nada (la garantía es **idempotencia**, no atomicidad: tras
un fallo parcial, re-correr el comando entero converge al mismo estado).

1. **Escribir el manifiesto (NUNCA se commitea si lleva valores reales).** Partir del canónico:
   ```bash
   cp tools/onboarding/clinica-demo.manifest.example.yaml tools/onboarding/mi-clinica.yaml
   # editar: identidad (slug/nombre/nit), admin, plan.features + features_override, branding,
   # secretos/config (opcionales; un agente por Kapso suele tener cero), packs.agenda / packs.faq,
   # canal.whatsapp.phone_number_id (lo da Kapso; no es secreto)
   ```
   `tools/onboarding/*.yaml` con valores reales va **gitignored**; solo se versiona el `*.example.yaml`.
   El loader acepta **YAML y JSON** (`yaml.safe_load`).

2. **Validación (falla cerrado).** Antes de tocar la BD, el provisionador valida contra
   `core/tenancy/catalogo.py`: features desconocidas → error; dependencias del set efectivo deben
   cumplirse; **coherencia flag↔datos** (declarar `packs.agenda` exige `pack_agenda` activa; ídem FAQ y
   WhatsApp); franjas `"HH:MM-HH:MM"`, días 0..6, tipo de recurso del enum y `recurso.presta` → servicio
   declarado. Si algo no valida, **no se escribe nada**.

3. **Exportar el entorno** (igual que el flujo JSON: `SECRETS_MASTER_KEY`, `CONTROL_DATABASE_URL`,
   `ADMIN_DATABASE_URL`, `TENANTS_DIRECT_URL_BASE`).

4. **Provisionar:**
   ```bash
   python -m tools.provision_from_manifest --from tools/onboarding/mi-clinica.yaml
   ```
   Imprime un resumen de una línea
   (`provision_manifest: mi-clinica OK -> 3 servicios, 2 recursos, 20 disponibilidad, 4 faq, wa:1176…`).
   Re-ejecutar es seguro (todo UPSERT por clave natural).

## Provisioning automatizado (operador) — lado fiscal/POS (JSON)

`tools/provision_tenant.py` cubre los pasos 2-6 de forma **idempotente** desde un JSON de onboarding:
crea la app DB, la registra en el control DB (URL cifrada), migra, siembra el admin, y carga
**secretos cifrados** (`secretos_empresa`), **config fiscal en claro** (`config_empresa`), **branding**
y el **`telegram_id`** real del admin.

1. **Rellenar el insumo (NUNCA se commitea).** Copiar el ejemplo y poner valores REALES:
   ```bash
   cp tools/onboarding/empresa.example.json tools/onboarding/puntorojo.json
   # editar puntorojo.json: slug, nombre, nit, admin.telegram_id, secretos (telegram_token,
   # matias_email, matias_password), config (matias_*), branding (color/logo/nombre/dominio),
   # plan + features_override (capacidades)
   ```
   `tools/onboarding/*.json` está en `.gitignore` (solo `empresa.example.json` se versiona). Los
   secretos van **cifrados** en el control DB con `SECRETS_MASTER_KEY`; nunca quedan en claro en git.

   **Capacidades (`plan` / `features_override`):**
   ```json
   "plan": { "nombre": "Pro", "features": ["facturacion_electronica", "fiados"] },
   "features_override": { "ventas_voz": false }
   ```
   - `plan.features` = capacidades del tier; `features_override` = excepciones por empresa
     (`true` activa, `false` desactiva). Efectivas = plan ∪ overrides activos − desactivados;
     el **núcleo** (ventas, inventario, caja, gastos, clientes, proveedores, reportes) está **siempre on**.
   - El provisioning **valida** contra `core/tenancy/catalogo.py`: nombres desconocidos → error; y las
     **dependencias** del set efectivo deben cumplirse (p. ej. `libro_iva` requiere `facturacion_electronica`
     o `compras_fiscal`). Si algo no valida, **no se escribe nada**.
   - **⚠️ Caveat — tier compartido:** el plan se upserta por **NOMBRE**. Cambiar `plan.features` de un
     nombre ya existente afecta a **todas** las empresas de ese plan. Para variar capacidades de una sola
     empresa, usa `features_override` (o un nombre de plan distinto). La consistencia es del operador.
   - Sin bloque `plan` ni `features_override`, la empresa arranca **solo con el núcleo**.

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
