# Prompt para Fable 5 — Planning de implementación
## Cliente: Construcciones PIM S.A.S. · Vertical de construcción sobre FerreBot SaaS

> **Cómo usar:** copia todo lo que sigue (desde "### 1. Rol") y dáselo a Fable 5 ejecutando **dentro del repo `ferrebot-saas`**, donde tiene acceso a la spec del cliente (`prospecto-pim/`) y a nuestra plataforma (`docs/`, `apps/`, `core/`, `modules/`, `tools/`).

---

### 1. Rol

Eres tech-lead / arquitecto trabajando **dentro del repositorio `ferrebot-saas`**. Vas a producir un **PLAN de implementación accionable** (todavía **no** código de features) de lo que **ya se puede construir** para el cliente **Construcciones PIM S.A.S.**, sabiendo que se decidió montarlo como un **tenant / vertical nuevo sobre nuestra plataforma**.

### 2. Objetivo

Entregar un plan por fases que **priorice lo desbloqueado** (lo que no depende de datos ni definiciones pendientes del cliente), respete nuestras reglas de arquitectura, y termine en una **Fase 0 lista para ejecutar**.

### 3. El cliente en corto (no inventes; amplía leyendo la spec)

Construcciones PIM S.A.S. (NIT 901462287): alquiler de maquinaria pesada y construcción de vías en asfalto, Colombia. Cotiza por **AIU** (Administración + Imprevistos + Utilidad) con **IVA 19% solo sobre la utilidad**; los % varían por contrato. **Márgenes reales 3–4 %** → detectar sobrecostos por obra **en tiempo real** es la razón de ser del sistema. Tiene trabajadores **directos** (quincenal, con todas las prestaciones) y **patacalientes** (por hora, informales, sin prestaciones). Un directo puede rotar por **2–3 obras en el mismo mes** → hay que **prorratear su costo por obra**. Las máquinas se alquilan por hora con un **mínimo facturable** (trabaja 3, cobra 5). Las compras de material generan **resbalo** (margen = precio de venta al cliente − costo del viaje). Los recibos de Bancolombia entran por **Telegram + IA (Claude Vision)**. Facturación y nómina electrónica ante la **DIAN vía MATIAS API** (modo Software Propio).

**Dolor extra detectado por nosotros (NO está en su spec): cartera de alquiler.** Le dan un cupo de crédito al cliente (p. ej. $10M), la máquina corre a $150.000/h, el cliente abona por cuotas, y la colita ($500K–$1M) se deja de pagar y se pierde. **Debes diseñar este módulo.**

### 4. Decisión de plataforma (firme)

- Se construye **sobre nuestra plataforma**, no como app standalone.
- Stack objetivo = **el nuestro**: Python 3.11 + FastAPI + SQLAlchemy + Alembic (backend), React + Vite (frontend), PostgreSQL **DB-per-tenant**, Redis/ARQ, MATIAS (DIAN), `python-telegram-bot`, Cloudinary.
- La spec del cliente está escrita en **Prisma + Next.js**: úsala como **fuente de verdad del dominio y de la lógica de negocio**, pero **porta** el modelo a SQLAlchemy **conservando los identificadores en español** tal cual aparecen.

### 5. Insumos a leer

Spec del cliente (ya ordenada por su contenido real):

- `prospecto-pim/spec-cliente/00_ARQUITECTURA_SISTEMA.md` … `16_ORDEN_DE_CONSTRUCCION.md`
- `prospecto-pim/skills-cliente/` — reglas del cliente (`colombia-compliance`, `money-safe`, `caveman`)
- `prospecto-pim/Brief-Prospecto-PIM.docx` — nuestro análisis fit-gap

Nuestra plataforma:

- `CLAUDE.md` y `.claude/rules/*` — reglas no negociables
- `docs/architecture.md`, `docs/tenancy.md`, `docs/schema.md`, `docs/data-model.md`
- `docs/facturacion-matias-extract.md`, `docs/facturacion-dian.md` — integración DIAN/MATIAS existente
- `docs/onboarding-tenant.md`, `tools/provision_from_manifest.py` — alta de tenant
- `docs/ai-tools.md` y `apps/bot/` — bot de Telegram + visión
- `modules/` y `core/` — qué ya existe (clientes, gastos, proveedores, compras, ventas, facturación, auth, tenancy)

### 6. Reglas de arquitectura que el plan DEBE respetar

- **Multi-tenant primero:** resolver el tenant y usar su sesión (`get_tenant_db()`); nunca cruzar datos. Las tablas de negocio **NO** llevan `empresa_id` (la base ES la frontera). Modelos de **control DB** vs. **app DB** separados.
- **Acceso a datos solo por repositorios;** nada de SQL suelto en routers/servicios.
- **`async`/`await`** en endpoints que emiten eventos en tiempo real.
- **Zona horaria Colombia (UTC-5)** siempre; nunca `date.today()` crudo.
- **Dinero = `Decimal(18,4)`;** toda fórmula monetaria en funciones **puras** bajo `services/calculations/`, con **una sola fuente de verdad por fórmula** (UI/Excel/PDF/bot la reusan); redondear solo al final; un único `formatCOP`.
- **Parámetros legales** (SMMLV, aux. transporte, IVA, % de aportes) en tabla `ParametrosLegales` con vigencia por fecha; **nunca hardcodear**. Confirmados 2026 por la spec: SMMLV 1.750.905, aux. transporte 249.095, IVA 19 %.
- **Secretos** por empresa **cifrados** en el control DB (MATIAS, token del bot, Cloudinary); nunca en git.
- **Logging estructurado** con `tenant_id` / `request_id`; nunca `print`.
- **Idempotencia** en operaciones críticas (venta / factura / webhooks / nómina).
- **Invariante:** nada mueve stock / caja / inventario sin su movimiento correspondiente.
- **Cadencia:** código-primero, tests al cierre de fase; **TDD test-primero SOLO** para los invariantes críticos (aislamiento multi-tenant, idempotencia, "nada mueve stock/caja sin movimiento").

### 7. Qué está DESBLOQUEADO (planificar y poder arrancar ya)

1. **Port del modelo de datos** (módulo 01) a SQLAlchemy + migración de tenant. Todo el esquema está especificado.
2. **Funciones puras + tests** (no requieren nada del cliente):
   - `calcular_totales_cotizacion` (AIU) — fórmula en módulo 03 y en skill `money-safe`.
   - `horas_facturables = max(horas_trabajadas, minimo)` — módulo 05.
   - `resbalo = precio_venta_cliente − costo_total_compra` — módulo 11.
   - `calcular_gasto_real_obra` (gastos + compras + prorrateo nómina + horas máquina + consumos) — módulo 04.
3. **Módulos CRUD totalmente especificados:** Clientes (02), Cotizaciones + builder (03), Obras (04), Máquinas (05), Herramientas (06), Empleados (07), Proveedores (10), Compras/resbalos (11), Gastos/caja (09). **Reusar/extender** lo que ya exista en `modules/`.
4. **Bot de Telegram + Claude Vision:** adaptar los flujos de PIM (recibo Bancolombia → Gasto; reporte diario de obra; horas de máquina) reusando el bot existente (módulo 14).
5. **Diseño del módulo nuevo "Cartera de alquiler"** (nuestro aporte).
6. **Motor de nómina** (08): construible con `ParametrosLegales`. Implementar la mecánica dejando los % como **parámetros configurables**; la validación de valores va aparte (ver §8).

### 8. Qué está BLOQUEADO (planificar, marcar la dependencia, NO cerrar)

- **Nómina — valores legales:** % de aportes (salud/pensión empleador, ARL por clase de riesgo, caja/SENA/ICBF), fondo de solidaridad por rangos, recargos de horas extra/dominicales, trato tributario de patacalientes → **validar con el contador de PIM**. Construir el motor parametrizado; no fijar números.
- **DIAN en vivo + nómina electrónica (CUNE):** requiere habilitación Software Propio de PIM + certificado digital + resolución de numeración + cuenta MATIAS / NIT propios. **Preparar contra el sandbox de MATIAS**; go-live gated.
- **Formato exacto de Excel/PDF de la cotización:** depende de las plantillas reales de PIM (refs PIM-010-2026). Hacer el motor de exportación con un formato provisional y ajustar al recibir la plantilla.
- **Migración de datos:** depende de insumos del cliente (clientes, máquinas, cotizaciones en Excel).
- **Insumos faltantes del cliente:** el **módulo 12 (Calculadora de rendimiento)** y los 3 archivos de **reglas de stack** no llegaron. Señálalo; no los inventes.

### 9. Entregable (produce esto, en este orden)

1. **Entendimiento** (10–15 líneas): confirma el alcance, la decisión de plataforma y los supuestos.
2. **Mapa de reuso** (tabla): por cada módulo del cliente (00–16 + Cartera de alquiler), marca **REUSA / EXTIENDE / NUEVO** frente a nuestra plataforma, con una nota corta.
3. **Port del modelo de datos:** lista de modelos SQLAlchemy a crear desde el módulo 01, con notas de mapeo Prisma→SQLAlchemy (`cuid()`→id, `@db.Decimal(18,4)`, enums, índices sugeridos) y confirmación de que todo va a la **app DB del tenant** (nada al control DB, salvo config/secretos). No generes aún todas las migraciones; define la estructura y el orden.
4. **Backlog por fases** (Fase 0..N). Cada fase: objetivo, entregable verificable, tareas concretas, tests (marca los invariantes como test-primero) y dependencias/bloqueos. **Front-load lo desbloqueado.**
5. **Funciones puras de cálculo:** firma + descripción + caso(s) de prueba de aceptación de cada una (AIU, horas facturables, resbalo, gasto real de obra; y el prorrateo de nómina parametrizado).
6. **Diseño de "Cartera de alquiler":** modelo de datos (cupo, consumo por horas, abonos, saldo) + reglas (cómo se descuenta el consumo, cómo se registra un abono, alerta de saldo estancado) + cómo se relaciona con Máquinas / Obras / Cliente / Factura.
7. **Riesgos y preguntas abiertas:** consolida los `[DEFINIR]` y bloqueos, e indica a quién preguntar (cliente / contador).
8. **Fase 0 ejecutable ya:** propuesta concreta (p. ej. provisionar el tenant PIM en dev con el manifiesto, scaffolding del vertical, port de los modelos base, 2–3 funciones puras con tests) lista para aprobación.

### 10. Restricciones de salida

- Es **planning**: no escribas código de features todavía; como mucho, stubs de firmas de funciones puras y nombres de modelos.
- **No inventes** valores legales ni de negocio marcados `[DEFINIR]`: déjalos como parámetros y señálalos.
- Respeta `CLAUDE.md`; si algo del spec del cliente choca con nuestras reglas (Prisma/Next.js, single-tenant, etc.), **dilo y propón el equivalente en nuestra plataforma**.
- **Español**, con los identificadores de dominio en español como en la spec.
- Concreto y accionable; sin relleno.
