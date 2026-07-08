# Contrato de arranque — Ola A del vertical construcción (PIM)

> **Qué es esto.** El insumo único para lanzar en paralelo la **Ola A** (Fases **2** cotizador AIU,
> **4** nómina, **6** bot/Vision) del plan `piped-hatching-sloth.md` **apenas termine la Fase 1**.
> Fija la numeración de migraciones, la propiedad de archivos por fase (para que 3 workflows Opus no
> se pisen), los contratos de API y qué prework ya está hecho y sólo se **consume**.
>
> **Fuente de verdad:** `~/.claude/plans/piped-hatching-sloth.md` §§3,4,5 + `prospecto-pim/spec-cliente/`
> (03 cotizaciones, 08 nómina, 14 bot) + `skill_money-safe.md`. Ante conflicto, manda el plan.
>
> **Autor:** agente T3 (prework Ola A). Sólo análisis; no toca código. Escrito 2026-07-06.

---

## 0. Estado observado del repo al escribir el contrato

Verificado leyendo el árbol (la Fase 1 estaba corriendo en el mismo working tree; algunos archivos
pueden renombrarse todavía —p. ej. `0046_extension_…` → `0046_ext_…`— pero los **hechos de contrato**
de abajo son estables):

| Hecho verificado | Evidencia |
|---|---|
| Migraciones de tenant llegan a **0046** | `migrations/tenant/versions/0043_construccion_base.py … 0046_ext_clientes_proveedores.py` |
| `cotizaciones_obra` + `items_cotizacion_obra` **ya creadas** | `0044_construccion_obra.py` crea `cotizaciones_obra`, `items_cotizacion_obra`, `obras`, `reportes_diarios_obra` |
| Modelos ORM de cotización/obra **ya existen** | `modules/obra/models.py`: `CotizacionObra`, `ItemCotizacionObra`, `Obra`, `ReporteDiarioObra`, `ConsumoInventario` |
| Modelos de máquina/asistencia **ya existen** | `modules/maquinaria/models.py` (`Maquina`, `AsignacionMaquinaObra`, `RegistroHorasMaquina`, `Mantenimiento`); `modules/trabajadores/models.py` (`Trabajador`, `AsignacionTrabajadorObra`, `RegistroAsistencia`) |
| `modules/nomina/` sólo tiene `ParametrosLegales` | `modules/nomina/models.py` → falta `PeriodoNomina`, `DetalleLiquidacion`, `ProrrateoNominaObra` (los pone Fase 4) |
| **Flags ya sembradas** (Fase 0) | `core/tenancy/catalogo.py`: `obras`, `maquinaria`, `herramientas`, `cotizaciones_aiu`, `nomina`, `nomina_electronica`, `cartera_alquiler`, `resbalos`, meta-pack `construccion` |
| Funciones puras desbloqueadas **listas** | `services/calculations/aiu.py`, `maquinas.py`, `resbalos.py` (con tests) |
| Motor de nómina: **stub tipado** | `services/calculations/nomina.py` → `Liquidacion`, `ProrrateoObra` + 3 firmas que hoy hacen `raise NotImplementedError` (ver §4) |
| Vision en `core/llm`: **aún no está** | `core/llm/base.py` `Message.content: str` (sin campo de imagen); `providers/{claude,openai}.py` sin soporte de imagen. Grep de `image/vision/base64` en `core/llm/` = 0 (ver §4) |
| `gastos`/`compras` **NO extendidas todavía** | `0046` sólo hace `add_column` sobre `clientes` y `proveedores`. Ninguna migración 0043–0046 toca `gastos`/`compras` (ver §5, riesgo crítico para Fase 6) |

**Precisión de dinero:** las tablas del vertical usan `MONEY4 = Numeric(18,4)` (`core/money.py`), distinto
del `MONEY = Numeric(12,2)` del POS. No mezclar. Todo cálculo de dinero pasa por `services/calculations/`
en `Decimal`, redondeo sólo al final (skill money-safe).

---

## 1. Numeración de migraciones de la Ola A

**Fase 1 deja el techo en `0046`.** La Ola A añade **una sola** migración.

| Fase | Migraciones nuevas | Tablas | Estado |
|---|---|---|---|
| **2 — Cotizador AIU** | **0 (cero)** | `cotizaciones_obra` + `items_cotizacion_obra` **ya las crea Fase 1 en `0044`** | ✅ confirmado leyendo `0044` |
| **4 — Nómina** | **`0047` (una)** | `periodos_nomina`, `detalles_liquidacion`, `prorrateo_nomina_obra` | 🔒 **RESERVADO para Fase 4** |
| **6 — Bot/Vision** | **0 (cero)** | escribe en `reportes_diarios_obra` (0044) y `registros_horas_maquina` (0045), ya existentes | ⚠️ pero el flujo de **gasto** depende de la extensión de `gastos` (ver §5) |

**Reglas de la numeración:**

- **`0047` es de la Fase 4 y de nadie más.** Fase 2 y Fase 6 **no escriben migraciones**. Si algún
  workflow de la Ola A cree que necesita una tabla nueva, **para y avisa al orquestador** — no tome 0047.
- `down_revision` de 0047 = `"0046_ext_clientes_proveedores"`. `revision` de 0047 **≤ 32 caracteres**
  (`alembic_version.version_num` es `VARCHAR(32)`; por eso 0046 abrevió su id). Sugerido:
  `revision = "0047_nomina_liquidacion"` (23 chars).
- **Backward-compatible obligatorio** (la tabla vive vacía en las empresas que no son PIM): sólo
  `create_table`, con `upgrade`/`downgrade` limpios probados en base efímera + `tools.migrate_tenants` en dev.
- Fase 4 debe **registrar sus 3 modelos ORM nuevos** en `modules/nomina/models.py` y en el mismo sitio
  de importación de modelos de tenant que usó Fase 1 para `ParametrosLegales` (mismo patrón por-módulo),
  para que SQLAlchemy mapee las clases al cargar la app.
- **La extensión de `gastos`/`compras` NO es de la Ola A.** Pertenece a la Fase 3 (Ola B) y tomará el
  siguiente número libre (tentativo **`0048`**). Ver el riesgo cruzado en §5.

---

## 2. Mapa de propiedad de archivos por fase (anti-colisión)

Principio: **cada fase escribe archivos DISJUNTOS.** Los modelos ORM quedaron **congelados por Fase 1**;
la Ola A los **importa read-only** y persiste por su propio repositorio. Los pocos archivos compartidos
(main.py, dispatcher, shell del dashboard) los cablea **un único integrador por ola** (§2.4).

### 2.1 Fase 2 — Cotizador AIU  → módulo NUEVO `modules/cotizacion_obra/`

Nombre elegido para **no chocar**: `modules/cotizaciones/` ya existe y es el quote **POS/WhatsApp**
(estados y semántica incompatibles, montado en `/api/v1/cotizaciones`). El vertical usa
`modules/cotizacion_obra/` (tabla `cotizaciones_obra`).

| Archivo | Rol |
|---|---|
| `modules/cotizacion_obra/repository.py` | Acceso a datos (importa `CotizacionObra`, `ItemCotizacionObra`, `Obra` de `modules/obra/models.py` — **read-only import**) |
| `modules/cotizacion_obra/service.py` | Builder, estados, numeración `PIM-0XX-AAAA`, conversión GANADA→Obra |
| `modules/cotizacion_obra/schemas.py` | Pydantic in/out |
| `modules/cotizacion_obra/router.py` | Endpoints (§3.1), `require_feature("cotizaciones_aiu")` |
| `modules/cotizacion_obra/errors.py` | Errores de dominio |
| `modules/cotizacion_obra/export_excel.py` · `export_pdf.py` | **Motor separado del formato** (formato provisional; se ajusta al recibir plantilla real — BLOQUEO parcial) |
| `dashboard/src/tabs/TabCotizacionesObra.jsx` (+ `.test.jsx`) | UI del builder (skills `ui-ux-pro-max`/`impeccable`) |

**Consume (no reimplementa):** `services/calculations/aiu.py::calcular_totales_cotizacion` (Fase 0, con
tests). Los totales AIU **nunca** se recalculan inline en router/service/Excel/PDF (money-safe).
**No crea** `modules/obra/{service,router}.py` (reservados a Fase 3): el shell de `Obra` en la conversión
se inserta desde `cotizacion_obra/repository.py` importando el modelo `Obra`.

### 2.2 Fase 4 — Nómina  → EXTIENDE `modules/nomina/`

En la Ola A **nadie más toca nómina/asistencia**, así que Fase 4 es dueña exclusiva de este módulo.

| Archivo | Rol |
|---|---|
| `modules/nomina/models.py` | **AÑADE** `PeriodoNomina`, `DetalleLiquidacion`, `ProrrateoNominaObra` (ya tiene `ParametrosLegales`) |
| `migrations/tenant/versions/0047_nomina_liquidacion.py` | La única migración de la Ola A (§1) |
| `modules/nomina/repository.py` · `service.py` · `schemas.py` · `router.py` · `errors.py` | Liquidación, cierre, pago, prorrateo |
| `dashboard/src/tabs/TabNomina.jsx` (+ `.test.jsx`) | UI de periodos + detalle individual |

**Consume (no reimplementa):** `services/calculations/nomina.py` (motor puro de T1 — ver §4);
`modules/trabajadores/models.py` (`Trabajador`, `RegistroAsistencia` — **read-only import**). La captura de
asistencia (crear filas `RegistroAsistencia`) la hace Fase 4 por su propio repositorio importando el
modelo; **no** edita `modules/trabajadores/` salvo consumir su repo si Fase 1 lo dejó.
**Fuera de alcance en Ola A:** transmisión DIAN / CUNE (endpoint `/transmitir-dian`) → es Fase 7, flag
`nomina_electronica`. No construir aquí.

### 2.3 Fase 6 — Bot PIM + Claude Vision  → `apps/bot/` + `ai/obra_tools.py`

En la Ola A **el árbol `ai/` es exclusivo de Fase 6** (Fase 2 y 4 no lo tocan).

| Archivo | Rol |
|---|---|
| `ai/obra_tools.py` | **NUEVO**: 3 tools con `rol_min` + `feature` (§3.3), patrón de `ai/cobranza_tools.py` |
| `apps/bot/…` (flujos PIM) | `/start` menú, flujo recibo Bancolombia→Vision→confirmar, reporte diario, horas de máquina |
| `ai/dispatcher.py` | **AÑADE** el registro del catálogo de `obra_tools` (append; F6 es el único que lo toca en la ola) |

**Consume (no reimplementa):** infra Vision de `core/llm` (T2 — ver §4); `services/calculations/maquinas.py`
`horas_facturables` (Fase 0) para la respuesta "6h registradas. Mínimo 5 cubierto. Ingreso hoy: $900.000";
`RegistroHorasMaquina` (`modules/maquinaria/models.py`) y `ReporteDiarioObra` (`modules/obra/models.py`),
persistidos por su propio repositorio (import read-only del modelo). **Storage:** Cloudinary (ya integrado
— resuelve el `[DEFINE R2/S3]` de la spec). **Bloqueo parcial:** el flujo de **gasto** (persistir `Gasto`
con `telegram_*`, `requiere_revision`, `origen_registro`, `obra_id`) depende de la extensión de `gastos`,
que **no está en la Ola A** (§5).

### 2.4 Archivos COMPARTIDOS peligrosos → disciplina de "integrador único"

Estos archivos los quieren tocar ≥2 fases; una edición concurrente = conflicto de merge garantizado.
**Regla:** las fases NO los editan; **un solo agente integrador de la Ola A** los cablea en un commit,
después de que F2/F4/F6 aterricen sus módulos disjuntos.

| Archivo compartido | Quién lo quiere | Disciplina |
|---|---|---|
| `apps/api/main.py` | F2 (`cotizacion_obra_router`) + F4 (`nomina_router`) | El integrador añade ambos `import` + `include_router(..., prefix="/api/v1")` juntos |
| `dashboard/src/routes.jsx`, `dashboard/src/App.jsx`, `dashboard/src/components/MobileNav.jsx` | F2 (`TabCotizacionesObra`) + F4 (`TabNomina`) | Cada fase crea su `TabXxx.jsx` disjunto; el integrador los enruta/añade al nav |
| `modules/obra/{service,router,repository}.py` | F2 (crea Obra) y F6 (crea ReporteDiario) los querrían | **RESERVADOS para Fase 3.** En Ola A NO se crean; cada consumidor persiste por su propio repo importando el modelo congelado |
| `ai/dispatcher.py` | Sólo F6 en la Ola A | F6 lo edita directo (no hay rival en la ola); append de su catálogo |
| `migrations/tenant/versions/` | Sólo F4 (0047) | F4 escribe; F2/F6 no |

> Recomendación operativa: el integrador puede ser el propio orquestador o un 4º agente lanzado **al
> cierre** de F2/F4/F6. Mientras tanto, cada fase entrega su módulo **compilando en aislamiento** y deja
> anotado en su PR el snippet exacto de `include_router` / tab a cablear.

---

## 3. Contratos de API por fase

Convenciones (verificadas en el repo): prefijo global `"/api/v1"`; gate de capacidad a nivel de router
con `Depends(require_feature("<flag>"))` (`core.auth.features`); gate de rol por endpoint con
`Depends(require_role("<rol>"))` (`core.auth`). Jerarquía de roles: **`super_admin` > `admin` > `vendedor`**
(el mapeo de los roles de la spec `CONTADOR/SUPERVISOR/OPERADOR` a estos lo fija Fase 1; por defecto
CONTADOR≈`admin`, SUPERVISOR≈`admin`, OPERADOR≈`vendedor`).

### 3.1 Fase 2 — Cotizador AIU  (router flag `cotizaciones_aiu`, prefijo `/cotizaciones-obra`)

> ⚠️ **No usar `/cotizaciones`**: ya lo ocupa el quote POS (`modules/cotizaciones`, mont. `/api/v1/cotizaciones`).

| Método + ruta | Rol mín. | Qué hace |
|---|---|---|
| `GET /api/v1/cotizaciones-obra` | vendedor | Lista con filtros `estado`/`cliente_id`/`fecha` |
| `POST /api/v1/cotizaciones-obra` | vendedor | Crea borrador (número `PIM-0XX-AAAA` autogenerado, editable) |
| `GET /api/v1/cotizaciones-obra/{id}` | vendedor | Detalle (incluye desglose interno oculto al cliente) |
| `PUT /api/v1/cotizaciones-obra/{id}` | vendedor | Edita builder (items dinámicos, %s AIU) |
| `POST /api/v1/cotizaciones-obra/{id}/estado` | vendedor | Marca `ENVIADA`/`GANADA`/`PERDIDA`/`VENCIDA` |
| `POST /api/v1/cotizaciones-obra/{id}/exportar-excel` | vendedor | Excel formato PIM (provisional) |
| `POST /api/v1/cotizaciones-obra/{id}/exportar-pdf` | vendedor | PDF profesional |
| `POST /api/v1/cotizaciones-obra/{id}/convertir-obra` | **admin** | Sólo si `GANADA`: crea `Obra` estado `PLANIFICADA`. **Idempotente** (1-1 con la cotización; no duplica Obra si ya convertida) |

Totales por `calcular_totales_cotizacion` (Fase 0). IVA sólo sobre la utilidad.

### 3.2 Fase 4 — Nómina  (router flag `nomina`, prefijo `/nomina`)

| Método + ruta | Rol mín. | Qué hace |
|---|---|---|
| `GET /api/v1/nomina/periodos` | admin | Lista de periodos |
| `POST /api/v1/nomina/periodos` | admin | Crea periodo (**congela snapshot** de `ParametrosLegales` vigente) |
| `GET /api/v1/nomina/periodos/{id}` | admin | Liquidación del periodo |
| `GET /api/v1/nomina/periodos/{id}/trabajador/{tid}` | admin | Detalle individual + **prorrateo por obra** |
| `POST /api/v1/nomina/periodos/{id}/liquidar` | admin | Liquida todos los activos. **Idempotente (test-primero)** |
| `POST /api/v1/nomina/periodos/{id}/cerrar` | admin | Bloquea edición del periodo |
| `POST /api/v1/nomina/periodos/{id}/pagar` | admin | Marca pagado. **Idempotente (test-primero)** |
| *(opcional)* `POST /api/v1/nomina/asistencia` | vendedor | Registra `RegistroAsistencia` del periodo |

**Excluido de la Ola A:** `POST …/transmitir-dian` (CUNE) → Fase 7 (`nomina_electronica`).
**Invariantes test-primero (§5 plan):** idempotencia de `liquidar`/`pagar`; **conciliación exacta del
prorrateo** (Σ `costo_imputado` ≡ costo total del trabajador, sin pérdida/duplicación). Valores legales
`[DEFINIR contador]`: el motor los lee del snapshot de `ParametrosLegales`, **nunca hardcode**.

### 3.3 Fase 6 — Bot PIM + Vision  (tools con `rol_min` + `feature`, gated por el dispatcher)

No expone API HTTP nueva propia (usa el webhook del bot). Sus unidades son **tools** en `ai/obra_tools.py`;
el dispatcher (`ai/dispatcher.py`) filtra por `satisface(ctx.rol, tool.rol_min)` y `ctx.tiene_capacidad(tool.feature)`.

| Tool | `feature` | `rol_min` | Efecto | Estado |
|---|---|---|---|---|
| `registrar_horas_maquina` | `maquinaria` | vendedor | Crea `RegistroHorasMaquina` (`origen_registro=TELEGRAM_BOT`); responde con `horas_facturables` (mínimo cubierto + ingreso del día) | ✅ **desbloqueado** (tabla en 0045) |
| `reporte_diario_obra` | `obras` | vendedor | Crea `ReporteDiarioObra` + URLs de fotos | ✅ **desbloqueado** (tabla en 0044) |
| `registrar_gasto_recibo` | `obras` | vendedor | Vision extrae JSON del recibo Bancolombia → `Gasto` (`origen_registro`, `telegram_*`, `requiere_revision` si `confianza<0.7`) | ⚠️ **bloqueado** por extensión de `gastos` (§5) |

Autorización del bot: sólo `telegram_user_id` vinculado de `Usuario` activo (reusa lo existente). Prompt
de Vision (spec 14): JSON estricto `{monto, fecha, hora, destinatario, numeroReferencia, concepto, confianza}`.

---

## 4. Prework que la Ola A CONSUME (no reescribe) — recordatorio al orquestador

Dos piezas de infraestructura **son de este prework** (agentes hermanos T1 y T2); Fases 4 y 6 las
**consumen**, no las reimplementan:

- **Motor de nómina puro — `services/calculations/nomina.py` (T1).** Contrato congelado: dataclasses
  `Liquidacion` y `ProrrateoObra` + firmas `liquidar_directo(trabajador, asistencia, params) -> Liquidacion`,
  `liquidar_patacaliente(horas, tarifa_hora) -> Liquidacion`,
  `prorratear_nomina_obra(liquidacion, dias_por_obra) -> list[ProrrateoObra]`. **Fase 4 tipa e invoca
  contra estas firmas; no las duplica** (money-safe: una fórmula = una fuente de verdad).
- **Infra Vision — `core/llm` (T2).** El vocabulario canónico (`Message`, `ToolSpec`, `LLMProvider`) vive
  en `core/llm/base.py`; el soporte de imagen se añade **ahí y en `providers/{claude,openai}.py`**. **Fase 6
  consume ese soporte; no reescribe la capa LLM** (cambio aditivo con tests de regresión del bot actual —
  riesgo §7.5 del plan).

> **Nota de estado honesta (2026-07-06).** Al escribir este contrato, `services/calculations/nomina.py`
> sigue siendo el **stub tipado** de Fase 0 (las 3 funciones hacen `raise NotImplementedError`) y
> `core/llm` **todavía no tiene** soporte de imagen (grep de `image/vision/base64` en `core/llm/` = 0).
> Es esperable si T1/T2 aún están corriendo. **Antes de lanzar Ola A, el orquestador debe confirmar que
> T1 dejó el motor real y T2 la infra Vision.** Fase 4 y Fase 6 **extienden/consumen**, jamás rehacen esos
> archivos. Si al arrancar siguen en stub, es un **bloqueo** de Fase 4/6, no una invitación a reimplementar.

---

## 5. Riesgos y dependencias cruzadas (leer antes de lanzar)

### 🔴 CRÍTICO — La extensión de `gastos`/`compras` no está en la Ola A → bloquea el flujo de gasto del bot

`0046` sólo extiende `clientes` y `proveedores`. Las columnas que el bot necesita en `gastos`
(`obra_id`, `origen_registro`, `telegram_message_id`, `telegram_user_id`, `requiere_revision`,
`categoria`, `metodo_pago`, `comprobante_url`) pertenecen a la **extensión de gastos/caja**, que el plan
asigna a la **Fase 3 (Ola B)** (§2 fila 09, §5 Fase 3) — número tentativo **0048**, fuera de la Ola A.

**Consecuencia:** en la Ola A, la tool `registrar_gasto_recibo` **no puede persistir el `Gasto` completo**
(ni la bandeja web de revisión de baja confianza, que cuelga de esas mismas columnas).

**Decisión que debe tomar el orquestador (elegir una):**
- **(A) Acotar Fase 6 en la Ola A** a lo 100% desbloqueado: `registrar_horas_maquina` + `reporte_diario_obra`
  + toda la **plomería de Vision** (extracción del recibo, confirmación en Telegram) — y **diferir la
  persistencia del `Gasto`** a que Ola B aterrice la extensión de `gastos` (0048, Fase 3). *(Recomendada:
  respeta la numeración fijada — 0047 sólo Fase 4 — y no bloquea a las otras dos tools.)*
- **(B) Adelantar** la extensión de `gastos`/`compras` como una migración backward-compatible **antes** de
  la Ola A (misma forma que 0046: puro `add_column`). Implicaría reasignar números (nómina pasaría a 0048).
  **Contradice** la numeración fijada en §1; sólo si el orquestador acepta re-secuenciar.

### 🟠 Colisión de prefijo POS vs. AIU
`modules/cotizaciones` (POS/WA) ya ocupa `/api/v1/cotizaciones`. Fase 2 **debe** usar `/cotizaciones-obra`
(o similar) y **módulo separado** `modules/cotizacion_obra/`. No extender el módulo POS.

### 🟠 `revision` id de 0047 ≤ 32 caracteres
`alembic_version.version_num` es `VARCHAR(32)`. Usar id corto (p. ej. `0047_nomina_liquidacion`).

### 🟡 Registro de modelos ORM nuevos (Fase 4)
Los 3 modelos de nómina deben quedar importados donde Fase 1 registró `ParametrosLegales`, o SQLAlchemy no
mapea las clases al cargar la app (queries fallan). Seguir el patrón por-módulo de Fase 1.

### 🟡 `modules/obra/{service,router}` reservado a Fase 3
Si F2 (crear Obra) o F6 (crear ReporteDiario) crean esos archivos, chocarán con Ola B. Persistir por el
repo propio de cada fase importando el modelo congelado.

### 🟡 Plan de control DB por nombre (riesgo §7.4 del plan)
El tenant PIM debe usar **plan con nombre único** (`construccion-pim`) o `features_override`; no reutilizar
un plan compartido por nombre. (Afecta al provisioning, no al código de la Ola A, pero vigilarlo en el corte.)

---

## 6. Checklist de arranque de la Ola A

- [ ] **Fase 1 terminada y mergeada** (modelos 0043–0046 estables; `modules/{obra,maquinaria,herramientas,trabajadores}` con sus CRUDs).
- [ ] **T1 confirmado**: `services/calculations/nomina.py` implementado (no stub) — si sigue stub, Fase 4 bloqueada (§4).
- [ ] **T2 confirmado**: `core/llm` con soporte de imagen — si no está, Fase 6 (Vision) bloqueada (§4).
- [ ] **Decisión del gasto del bot** tomada: opción (A) o (B) de §5 🔴.
- [ ] Los 3 workflows arrancan con su **mapa de archivos disjunto** (§2) y saben que **no tocan** `apps/api/main.py`, el shell del dashboard ni `modules/obra/{service,router}`.
- [ ] `0047` reservado a Fase 4; Fase 2 y Fase 6 con **cero migraciones**.
- [ ] Integrador único designado para cablear `main.py` + nav del dashboard al cierre.
- [ ] Tests test-primero acordados: aislamiento multi-tenant (toda tabla nueva), idempotencia (convertir-obra, liquidar, pagar, registrar horas del bot), conciliación del prorrateo.
