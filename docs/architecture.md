# Arquitectura — FerreBot SaaS

> Plan técnico completo. Plataforma POS multi-empresa alineada con FerreBot, diseñada para venderse a otras empresas. Punto Rojo es el primer tenant.

## 1. Objetivo

POS moderno para ferreterías y comercios, accesible desde un **dashboard web** y desde un **agente IA en Telegram**, capaz de ejecutar operaciones reales del negocio. Replica y mejora todo lo que FerreBot hace hoy, y nace como **SaaS**: una sola plataforma que da servicio a muchas empresas, cada una con sus datos aislados.

Gestiona, por empresa: ventas (normal, rápida, varia, fiado), inventario y kardex, compras y compras fiscales, gastos, caja, clientes, proveedores, fiados, honorarios, facturación electrónica DIAN (factura, documento soporte, notas), libro IVA, reportes y operaciones por lenguaje natural (texto y voz).

## 2. Decisiones clave

| Tema | Decisión |
|---|---|
| Interfaz IA | Agente en Telegram + dashboard web |
| Frontend | React + Vite (white-label por empresa) |
| Capa de datos | SQLAlchemy + Alembic sobre PostgreSQL |
| Arquitectura IA | Híbrido: bypass Python + function calling (posible OpenAI) |
| Multi-tenancy | **Una base de datos por empresa** (DB-per-tenant) |
| Alcance SaaS | Multi-tenant desde día 1; lanzar con Punto Rojo |
| Cobro a empresas | Manual/informal ahora (transferencia/Nequi); billing enchufable después (sin Stripe por ahora) |
| Resiliencia | **Modo offline** (PWA + cola) — prioridad alta |
| Conexiones | **PgBouncer** (pooling) obligatorio por el modelo DB-per-tenant |
| Removido | Keepalive, Wompi, Bold, hardware de POS (este último, futuro) |
| Módulos opcionales | **Feature flags por empresa** (fiscal/contable activable; ver `feature-flags.md`) |

## 3. Filosofía

- **La IA no es responsable de la lógica del negocio.** Es interfaz: entiende lenguaje natural, interpreta, consulta y ejecuta herramientas. La lógica crítica (precios, stock, impuestos, consecutivos DIAN) es determinística en el backend.
- **Ruta rápida sin IA:** ~60% de las ventas simples se resuelven en Python puro (`bypass`) en <5 ms.
- **Determinismo y aislamiento:** el modelo nunca toca la base; invoca una herramienta que valida permisos, resuelve la empresa y deja auditoría.

## 4. Stack

- Backend: Python 3.11+, FastAPI, Uvicorn (uvloop)
- ORM/migraciones: SQLAlchemy + Alembic (multi-base)
- Base de datos: PostgreSQL — 1 control DB + 1 app DB por empresa, detrás de PgBouncer
- Frontend: React + Vite (PWA con modo offline), estáticos servidos por FastAPI
- Bot: python-telegram-bot (webhook), un bot por empresa
- IA: Claude + OpenAI (Whisper voz, function calling)
- Cola/caché: Redis + ARQ
- Tiempo real: pg_notify -> SSE (por empresa)
- Facturación: MATIAS (UBL 2.1 DIAN), credenciales por empresa
- Imágenes: Cloudinary (por empresa)
- Observabilidad: logs estructurados + Sentry + /health
- Despliegue: Railway

## 5. Arquitectura general

Dos servicios (bot y api) en el mismo repo, más una separación entre **plano de control** (catálogo de empresas, planes, secretos) y **plano de datos** (una base por empresa).

```
                       Telegram (1 bot por empresa)
                                  | webhook /tg/{empresa}
 empresa1.app.com ┐               v
 empresa2.app.com ┼── HTTPS ──> Servicio API (FastAPI)
 (subdominio)     ┘     ├── Middleware de tenant (resuelve la empresa)
                        ├── Router de conexiones (PgBouncer -> base de la empresa)
                        ├── Módulos de dominio (ventas, inventario, caja, ...)
                        ├── Agente IA + SSE (por empresa)
                        └── Dashboard React PWA (white-label, offline)
                                  |
        ┌─────────────────────────┼──────────────────────────┐
        v                         v                           v
   Control DB                App DB empresa 1          App DB empresa 2
   - empresas / planes       - productos, ventas        - productos, ventas
   - secretos cifrados       - caja, facturación        - caja, facturación
   - billing / branding      (esquema de negocio)       (esquema de negocio)
```

## 6. Multi-tenant: una base por empresa

- **Resolución de empresa:** por subdominio (`empresa.app.com`) o por `tenant_id` en el JWT; un middleware la resuelve en cada request y la valida contra el control DB (con caché).
- **Enrutamiento de conexiones:** el control DB guarda la conexión de cada empresa; la API mantiene un caché de engines (uno por empresa activa) con límites y evicción. Todo pasa por **PgBouncer** para no agotar las conexiones de Postgres (clave del modelo DB-per-tenant).
- **Migraciones multi-base:** dos árboles Alembic (`control/`, `tenant/`); un runner aplica las migraciones de negocio a todas las empresas en cada despliegue.
- **Aprovisionamiento:** crear base -> migrar -> sembrar -> cargar secretos cifrados y branding -> crear admin -> asignar subdominio. Ver `onboarding-tenant.md`.
- **Tradeoffs:** máximo aislamiento, backups/restore por empresa y radio de impacto acotado, a cambio de migraciones sobre N bases y más provisioning (todo automatizado). Muchas bases en una instancia al inicio; instancias dedicadas para clientes grandes.

## 7. Organización del código

Monolito modular con capas limpias. Regla de dependencia: el dominio no conoce la infraestructura.

- **Routers** (FastAPI): validan, resuelven empresa y permisos; sin lógica de negocio.
- **Servicios** (dominio): lógica pura y testeable; sin SQL directo.
- **Repositorios** (datos): único lugar que conoce la base (SQLAlchemy).
- **Modelos/esquemas:** SQLAlchemy + Pydantic.

Módulos por dominio autocontenidos: `ventas · inventario · caja · gastos · compras · clientes · proveedores · fiados · honorarios · facturacion · reportes · ia · tenancy · auth`. Ver `system-design.md` para la estructura de carpetas.

## 8. Agente IA

- **Híbrido:** bypass Python para lo simple; function calling cuando hay que interpretar (proveedor agnóstico: OpenAI o Claude).
- **Herramientas (ya en FerreBot):** `registrar_venta`, `registrar_gasto`, `registrar_fiado`, `abonar_fiado`, `crear_cliente`. A consolidar: `buscar_producto`, `consultar_stock`, `consultar_cliente`, `abrir/cerrar/consultar_caja`, `registrar_compra`, `emitir_factura`, `generar_reporte`.
- **Memoria operacional (RAG):** sin memoria permanente; el contexto se arma desde la base de la empresa en cada turno (`memoria_entidades`, `conversaciones_bot`, `memoria_turno`, `price_cache`).
- **Voz:** Whisper (`audio_sales`, `ventas_pendientes_voz`, `audio_logs`, `voz_filtros`).
- **Búsqueda:** fuzzy, aliases (typos), catálogo semántico, full-text.
- **Costo de IA:** medido por empresa (`api_costo_diario`, `ai/budget`).

## 9. Modelo de datos

Resumen aquí; detalle en `data-model.md`.

- **Control DB:** `empresas`, `tenant_databases`, `planes`, `suscripciones`, `secretos_empresa` (cifrados), `branding`, `super_admins`.
- **App DB por empresa** (idéntico a FerreBot, sin columna de empresa): catálogo/inventario, ventas, compras, caja/gastos, clientes/proveedores, fiados/honorarios, facturación DIAN, IVA, IA/bot, usuarios/config.

Reglas: nunca modificar stock sin movimiento de inventario, ni caja sin movimiento de caja.

## 10. Módulos del negocio

- **Inventario:** fracciones (1/2, 1/4), precio mayorista, IVA por producto (0/5/19), kardex, alertas de stock bajo.
- **Ventas:** rápida, varia, a crédito (fiado), anulación con reversa de stock, bypass.
- **Compras/caja/gastos:** compras -> ENTRADA de inventario; compras fiscales; factura por foto (Cloudinary) o correo (Gmail); caja con arqueo; gastos categorizados que mueven caja.
- **Clientes/proveedores/fiados/honorarios:** datos + campos fiscales; historial; abonos y saldos; cuentas de cobro.

## 11. Facturación electrónica DIAN

MATIAS (UBL 2.1) con credenciales, resolución y consecutivos **por empresa**. Flujo: Venta -> Factura pendiente -> MATIAS -> DIAN -> CUFE -> PDF + XML -> Cliente. La venta queda registrada aunque la DIAN tarde (emisión **asíncrona con reintentos**, ver §13). Componentes: factura, documento soporte (DS-NO, resolución propia), notas crédito/débito, eventos DIAN, libro IVA y saldos bimestrales. `city_id` de MATIAS != código DANE (mantener caché DANE -> ID interno por empresa).

## 12. Dashboard, tiempo real e integraciones

- **Dashboard (React + Vite, PWA):** mismo diseño que FerreBot con tema por empresa; tabs (Hoy, Ventas rápidas, Inventario, Kardex, Clientes, Proveedores, Compras, Compras fiscal, Gastos, Caja, Facturación, Facturas recibidas, Libro IVA, Historial, Resultados, Top productos); ChatWidget, CommandPalette; auth JWT + Telegram Login.
- **Tiempo real (SSE):** por empresa (pg_notify -> listener -> SSE -> `useRealtime`), eventos en snake_case.
- **Integraciones (por empresa):** MATIAS, Cloudinary, Gmail (compras), Bancolombia (transferencias), Telegram. **Removidos:** Wompi, Bold, keepalive.

## 13. Resiliencia, idempotencia y jobs (del audit)

- **Modo offline (prioridad alta):** el dashboard es una **PWA** con cola offline (IndexedDB). Si se cae el internet, sigue registrando ventas y sincroniza al reconectar. Imprescindible para un POS en tienda.
- **Idempotencia:** operaciones críticas (venta, emisión de factura, webhooks de pago) usan **clave de idempotencia** para no duplicar en reintentos del bot, de la cola offline o de webhooks.
- **Jobs en background (desde el inicio):** Redis + ARQ para emisión DIAN asíncrona con **reintentos y dead-letter**, provisioning de empresas y el runner de migraciones. Reconciliación periódica del estado DIAN.
- **Email transaccional:** envío de factura/recibo al cliente por correo (antes que WhatsApp).

## 14. Seguridad, roles y cumplimiento

- Roles: `super_admin` (operador SaaS) > `admin` (empresa) > `vendedor` (`cajero`/`supervisor` como expansión).
- Aislamiento por base; secretos por empresa cifrados; auth `@protegido` (bot) y JWT (API); auditoría por empresa.
- **Backups y DR (del audit):** PITR, retención y **pruebas de restauración**; respaldo por empresa. Histórico fiscal DIAN ~5 años.
- **Compliance Colombia (anotado, futuro):** Habeas Data (Ley 1581) cuando haya empresas-cliente reales. Ver `/SECURITY.md`.

## 15. Cobro a las empresas (billing)

Por ahora **manual/informal**, sin comisión de pasarela: cobro por transferencia / Nequi / Daviplata / Bancolombia y un **estado de suscripción** por empresa en el control DB (`activa` / `suspendida` / `vencida`) que se cambia a mano. Diseñado como **módulo enchufable**: el día que haya varios clientes, se conecta un proveedor (en Colombia, evaluar Wompi / Mercado Pago / PSE antes que Stripe para evitar comisión en USD).

## 16. Observabilidad y operación

- `/health` (verifica control DB y dependencias) y `/ready`; monitor de uptime externo (reemplaza el keepalive).
- Logs estructurados con `tenant_id` y `request_id`; errores en Sentry con contexto de empresa; métricas por empresa.
- Operación detallada (provisioning, migraciones N-bases, restore) en `runbook.md`.

## 17. Plan de migración (Punto Rojo = tenant #1)

La migración de FerreBot es el primer aprovisionamiento. **Copiar datos:** productos/inventario, clientes, proveedores, histórico legal DIAN (preservando consecutivos y resolución MATIAS), saldos IVA, usuarios/config. **Reconstruir:** ventas (histórico opcional), caja, gastos, compras, fiados (solo saldos vivos), datos operativos de IA.

Fases: (1) control DB + esquema tenant con Alembic; (2) provisionar Punto Rojo; (3) copiar datos de referencia; (4) migrar histórico DIAN; (5) portar servicio MATIAS (caché `_get_city_id`) + secretos; (6) portar bypass y `ai/tools.py`; (7) conectar bot y dashboard; (8) tests y paridad; (9) corte de webhooks.

## 18. Hoja de ruta

1. Monolito modular + control DB + app DB por empresa + PgBouncer; Punto Rojo en producción; PWA offline.
2. Redis + ARQ (jobs, emisión DIAN con reintentos); email transaccional; backups/DR probados.
3. Billing enchufable + segunda empresa-cliente; medición de planes/cuotas.
4. Extraer servicios (IA, facturación); instancias de DB dedicadas para clientes grandes; multi-bodega y analítica. (Futuro: hardware POS, WhatsApp, Habeas Data.)
