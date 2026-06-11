# Capacidades por empresa (feature flags)

> No todas las empresas necesitan lo fiscal/contable. Cada empresa activa solo las capacidades que usa. Esquema en `schema.md` (control DB); exposición en `api-contract.md`.

## Principio

- **El esquema NO cambia entre empresas.** Todas las app DB tienen las mismas tablas (incluidas las fiscales). Las flags **no crean ni quitan tablas**: solo deciden qué se **expone y ejecuta**. Esto mantiene las migraciones uniformes y simples; una tabla vacía no cuesta nada.
- Lo que las flags controlan: endpoints de la API, tabs y campos del dashboard, comandos del bot y jobs (p. ej. emisión DIAN).

## Catálogo de capacidades

**Núcleo (siempre activo):** `ventas`, `inventario`, `caja`, `gastos`, `clientes`, `proveedores`, `reportes`.

**Opcionales:**

| Capacidad | Qué habilita | Depende de |
|---|---|---|
| `facturacion_electronica` | Emitir factura DIAN (MATIAS), tab Facturación, campos fiscales de cliente | — |
| `documento_soporte` | DS-NO (compras a no obligados), resolución propia | — |
| `notas_electronicas` | Notas crédito/débito | `facturacion_electronica` |
| `libro_iva` | Tab Libro IVA, saldos bimestrales | `facturacion_electronica` o `compras_fiscal` |
| `compras_fiscal` | Compras con soporte tributario, tab Compras fiscal | — |
| `honorarios` | Cuentas de cobro | — |
| `fiados` | Crédito a clientes y abonos | — |
| `pack_cobranza` | Agente de cobranza por WhatsApp: recordatorios de cartera, promesas de pago, página Cartera (ADR 0015) | `fiados` |
| `mayorista` | Precio mayorista por producto | — |
| `ventas_voz` | Ventas por audio (Whisper) en el bot | `bot_telegram` |
| `bot_telegram` | Agente en Telegram | — |
| `multi_vendedor` | Más de un vendedor + filtros por vendedor | — |

Las dependencias se validan al activar (no se puede activar `notas_electronicas` sin `facturacion_electronica`).

## Almacenamiento (control DB)

- El **plan** define el set por defecto: `planes.limites.features = [...]`.
- Las **excepciones por empresa** viven en `empresa_features` (activar/desactivar sobre el plan).
- **Capacidades efectivas** = (features del plan) ± (overrides de `empresa_features`). Se calculan una vez y viajan en el contexto del tenant (cacheadas, ver `tenancy.md` §3).

## Enforcement (cómo se aplica en cada capa)

1. **Backend (API):** dependencia `require_feature("facturacion_electronica")` en los routers fiscales. Si la empresa no la tiene → **404** (como si la ruta no existiera). Las capacidades se cargan con el contexto de empresa.
2. **Frontend (dashboard):** el endpoint de arranque (`GET /api/v1/config`) devuelve `features`. El dashboard **oculta tabs** (Facturación, Libro IVA, Compras fiscal, Honorarios) y **campos** (datos fiscales de cliente/compra) según las flags.
3. **Bot:** los comandos fiscales (`/factura_electronica`, etc.) verifican la flag; si está off, responden "no habilitado".
4. **Jobs:** la emisión DIAN y la conciliación solo corren para empresas con `facturacion_electronica`.

## Campos fiscales condicionales

Algunos campos existen siempre en el esquema (nullable) pero **solo se muestran/piden** si la flag está activa: en `clientes` (`regimen`, `ciudad_dane`), en compras (datos de `compras_fiscal`). Una empresa sin lo fiscal nunca los ve.

## Administración

- En el **provisioning** se siembran las capacidades desde el plan.
- El `super_admin` ajusta por empresa: `PUT /api/v1/admin/empresas/{id}/features`.
- Cambiar una flag invalida la caché del tenant (efecto inmediato).

## Ejemplos

- **Ferretería con contador (Punto Rojo):** núcleo + `facturacion_electronica`, `documento_soporte`, `notas_electronicas`, `libro_iva`, `compras_fiscal`, `honorarios`, `fiados`, `mayorista`, `bot_telegram`, `ventas_voz`.
- **Tienda simple sin facturación:** solo núcleo + `fiados` + `bot_telegram`. No ve Facturación, Libro IVA ni Compras fiscal; las tablas existen pero quedan vacías.
