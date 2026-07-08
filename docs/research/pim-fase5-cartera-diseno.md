# Fase 5 — Cartera de alquiler: diseño de implementación

> Insumo directo para el workflow de Fase 5 (Ola B, arranca cuando exista `RegistroHorasMaquina` de
> Fase 3). Módulo **nuestro** (NO está en la spec del cliente). Fundamento: plan maestro
> `piped-hatching-sloth.md` §6, y el **código real** que se reutiliza —`modules/fiados/` (ledger de
> crédito), `modules/cobranza/` (pack_cobranza), `modules/pagar/` (aviso interno al dueño vía SSE) y el
> runtime de crons ARQ en `apps/worker/main.py`.
>
> Principio rector (plan §6): **reusar el ledger de `fiados` y los recordatorios de `pack_cobranza` en
> vez de duplicar el saldo.** El saldo vive donde ya vive: `fiados_movimientos` + el contador
> denormalizado `clientes.saldo_fiado`.

## 0. Hechos del código real (anclas)

Verificados leyendo el repo (no inventados):

**Ledger de fiados** (`modules/fiados/`):
- `models.py`: `Fiado(id, cliente_id, venta_id, monto, saldo, idempotency_key, creado_en)` y
  `FiadoMovimiento(id, fiado_id, tipo, monto, idempotency_key, creado_en)`. `tipo` es el enum PG
  `fiado_mov_tipo` con literales `"cargo"`/`"abono"`. Dinero = `MONEY = Numeric(12, 2)`.
- `saldo.py`: `nuevo_saldo(saldo_anterior, tipo, monto)` (cargo suma, abono resta), `excede_saldo(...)`.
  Constantes `CARGO = "cargo"`, `ABONO = "abono"`.
- `repository.py` (`SqlFiadosRepository`): `lock_cliente`, `lock_fiado`, `fiado_por_key(idempotency_key)`,
  `movimiento_por_key(idempotency_key)`, `crear_fiado(cliente_id, venta_id, monto, idempotency_key)`,
  `abonar(fiado, monto, idempotency_key)`, `deudas()`. `crear_fiado` inserta el `Fiado` (saldo=monto),
  su `FiadoMovimiento` **cargo** y hace `UPDATE clientes SET saldo_fiado = saldo_fiado + :m` en la
  **misma transacción** (dual-write atómico); publica `fiado_registrado`. `abonar` publica `fiado_abonado`.
- `service.py` (`FiadosService`): `crear(cliente_id, venta_id, monto, idempotency_key) -> ResultadoFiado(fiado, replay)`
  e `abonar(fiado_id, monto, idempotency_key) -> ResultadoAbono(movimiento, replay)`. **Idempotencia
  existente**: `crear` serializa con `lock_cliente` y consulta `fiado_por_key` DENTRO de la sección
  crítica; `abonar` serializa con `lock_fiado` y consulta `movimiento_por_key`. Sobre-abono → `SobreAbono` (422).
- Nota: `fiados.idempotency_key` y `fiados_movimientos.idempotency_key` **no tienen índice único hoy**;
  la idempotencia se apoya en la serialización por lock de la fila ancla.

**Pack cobranza** (`modules/cobranza/`):
- El saldo **no vive aquí**: el motor solo LEE `clientes.saldo_fiado`. `CobranzaService.procesar_recordatorios(ahora, enviar)`
  barre `repository.deudores(saldo_minimo)` (clientes con `saldo_fiado > mínimo`), aplica cadencia, tope,
  ventana horaria, opt-out y promesas. `crear_promesa`, `promesa_vigente`, `cerrar_al_dia`, `recuperado`.
- **Un cliente con `saldo_fiado > saldo_minimo` YA es candidato del motor de cobranza** — sin registrarlo
  en ninguna parte. Esto es clave para "la colita entra al ciclo de recordatorios" (§4).

**Pack pagar — aviso interno al dueño vía SSE** (`modules/pagar/`, `apps/worker/main.py`):
- El aviso al dueño NO usa plantilla de WhatsApp: se emite con `core.events.publisher.publish(session, "pagar_aviso", {...})`
  **en la misma transacción del tenant** (pg_notify transaccional: viaja solo al COMMIT). El dashboard
  reacciona al evento. Ver `_hacer_enviar_pagar(session)` en `apps/worker/main.py`.
- El router (`modules/pagar/router.py`) va gateado por `require_feature("pack_pagar")` (sin flag → 404)
  y **todo es rol `admin`** (dato sensible del negocio). Config de una fila get-or-create (`PagarConfig`),
  estado de dedup por factura (`PagarAviso`).

**Runtime de crons ARQ** (`apps/worker/main.py`, `WorkerSettings.cron_jobs`):
- Patrón de cron multi-tenant: función `async def job(ctx) -> str`, lista `tenants`/`numeros` desde el
  control DB, filtra por capacidad (`ControlCapacidades(cs).efectivas(id)` o `t.features`), abre
  `tenant_session(tenant)` (commit al cerrar el generador), corre el motor con `now_co()`, y `try/except`
  por tenant para que **un fallo no tumbe el barrido**. Se registra con `cron(job, hour={..}, minute={..}, run_at_startup=False)`.
  ARQ corre en la hora del **servidor (UTC en Railway)** — las fechas relativas se derivan con `now_co()`.
- `avisos_pagar` es el molde exacto para el aviso interno: barre `listar_tenants`, filtra `pack_pagar`,
  publica el SSE dentro de la sesión del tenant.

**Vertical construcción — tablas ya migradas (0043–0046), que la cartera CONSUME (Fase 1/Ola A):**
- `obras` (0044): `id, cotizacion_id (1-1 nullable), cliente_id (FK clientes, NOT NULL), nombre,
  ubicacion, fecha_inicio, fecha_fin_estimada, fecha_fin_real, estado, notas, ..., eliminado_en`.
  `estado` = enum `estado_obra` con literales `PLANIFICADA, EN_EJECUCION, SUSPENDIDA, FINALIZADA, LIQUIDADA`.
- `asignaciones_maquina_obra` (0045): `id, maquina_id, obra_id, fecha_inicio, fecha_fin,
  precio_hora (MONEY4), minimo_horas (Integer), operador_id, activa (bool)`. **El precio y el mínimo
  viven POR ASIGNACIÓN** (pueden diferir del default de la máquina).
- `registros_horas_maquina` (0045): `id, maquina_id, obra_id, fecha, horas_trabajadas (18,4),
  horas_facturables (18,4), operador_id, observaciones, origen_registro, creado_en`. **OJO: no tiene
  `asignacion_id`** — la asignación se resuelve por `(maquina_id, obra_id)` activa que cubra `fecha`.
- Dinero del vertical = `MONEY4 = Numeric(18, 4)` (`core/money.py`); el ledger de fiados es `MONEY = Numeric(12, 2)`.
  **Frontera de precisión**: el cargo se calcula en 18,4 y se asienta en el ledger 12,2 → `cuantizar(...)`
  en el borde (ver §2, riesgo en §7). El plan advierte no mezclar ambas precisiones.
- Flag `cartera_alquiler` **ya existe** en `core/tenancy/catalogo.py` (OPCIONALES) con dependencia dura
  `cartera_alquiler → fiados` (`DEPENDENCIAS`) y pertenece al meta-pack `construccion`.

## 1. Modelo de datos

### 1.1 `cupos_alquiler` (tabla nueva, dueña: `modules/cartera_alquiler/`)

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `BigInteger` PK | |
| `cliente_id` | `BigInteger` FK `clientes.id` NOT NULL | |
| `cupo` | `MONEY4` (`Numeric(18,4)`) NOT NULL | tope de crédito de alquiler (ej. $10.000.000) |
| `vigente_desde` | `Date` NOT NULL | |
| `vigente_hasta` | `Date` NULL | NULL = sin vencimiento |
| `activo` | `Boolean` NOT NULL default `true` | |
| `notas` | `Text` NULL | |
| `creado_en` / `actualizado_en` | `TIMESTAMP(tz)` | `server_default now()` / `onupdate now()` |

**Invariante "un cupo activo por cliente"** → índice **único parcial**:
`CREATE UNIQUE INDEX uq_cupos_alquiler_cliente_activo ON cupos_alquiler (cliente_id) WHERE activo`
(mismo patrón que el "unique carrito" de la auditoría 2026-07). Cambiar de cupo = desactivar el vigente
y crear otro (histórico por `vigente_desde/hasta`).

### 1.2 Relación con el ledger de fiados (NO se duplica el saldo)

El **saldo consumido NO se guarda en `cupos_alquiler`**. La fuente de verdad sigue siendo
`fiados_movimientos` (Σ cargos − Σ abonos) y el contador `clientes.saldo_fiado`. `cupos_alquiler`
solo aporta el **tope**; el consumo lo aporta el ledger:

```
consumido(cliente)  = clientes.saldo_fiado          (lectura del ledger existente)
disponible(cliente) = cupo − consumido
```

Para PIM esto es exacto: un cliente de construcción **no compra POS a crédito**, así que su `saldo_fiado`
ES su saldo de alquiler. Para un tenant mixto (POS + alquiler) hay una imprecisión — ver §7 (riesgo de
conciliación) y el uso de `cargos_alquiler` para aislar el consumo de alquiler por obra.

### 1.3 `cargos_alquiler` (tabla nueva de traza/enlace — recomendada)

Enlaza cada `RegistroHorasMaquina` con el cargo que generó en el ledger. Da **idempotencia dura** (por el
`UNIQUE`) y **trazabilidad** (obra/máquina/asignación/registro), sin tocar el esquema compartido de fiados.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | `BigInteger` PK | |
| `registro_horas_id` | `BigInteger` FK `registros_horas_maquina.id` NOT NULL **UNIQUE** | ancla de idempotencia |
| `fiado_id` | `BigInteger` FK `fiados.id` NOT NULL | el cargo asentado |
| `obra_id` | `BigInteger` FK `obras.id` NOT NULL | agrupa la vista de cartera por obra |
| `maquina_id` | `BigInteger` FK `maquinas.id` NOT NULL | |
| `asignacion_id` | `BigInteger` FK `asignaciones_maquina_obra.id` NOT NULL | precio/mínimo aplicados |
| `monto` | `MONEY` (`Numeric(12,2)`) NOT NULL | ya cuantizado al ledger |
| `creado_en` | `TIMESTAMP(tz)` | |

`UNIQUE(registro_horas_id)` = **"un `RegistroHorasMaquina` no genera dos cargos"** a nivel de base.

### 1.4 `cartera_config` (tabla nueva, una fila get-or-create — patrón `cobranza_config`/`pagar_config`)

| Columna | Tipo | Default | Notas |
|---|---|---|---|
| `id` | `BigInteger` PK | | |
| `activo` | `Boolean` | `true` | apaga la detección de colita |
| `dias_colita` | `Integer` | `15` | N días sin abono para marcar colita (plan §6b) |
| `cadencia_aviso_dias` | `Integer` | `7` | no re-avisar la misma colita antes de N días (dedup) |

## 2. Consumo: RegistroHorasMaquina → cargo en el ledger (idempotente)

### 2.1 Flujo (síncrono, misma transacción que el registro de horas)

Al registrar un `RegistroHorasMaquina` (Fase 3) cuya `(maquina_id, obra_id)` tiene **asignación activa**
y el tenant tiene `cartera_alquiler` activa:

1. `monto = cuantizar(horas_facturables × asignacion.precio_hora)` — **`cuantizar` cruza la frontera
   18,4 → 12,2** (core/money.cuantizar). `horas_facturables` ya viene con el mínimo aplicado (0045).
2. **Idempotencia pre-check**: ¿existe `cargos_alquiler.registro_horas_id`? → sí: replay, no se asienta nada.
3. Asentar el cargo **reutilizando la función existente**
   `FiadosService.crear(cliente_id=obra.cliente_id, venta_id=None, monto=monto,
   idempotency_key=f"alquiler:horas:{registro_horas_id}")`. Esto inserta `Fiado`+`FiadoMovimiento`(cargo)
   y suma `clientes.saldo_fiado` — todo atómico y ya probado.
4. Insertar la fila `cargos_alquiler(registro_horas_id, fiado_id, obra_id, maquina_id, asignacion_id, monto)`.
5. **Chequeo de cupo** (no bloquea): si `saldo_fiado_after > cupo_activo` → `publish(session, "cartera_cupo_excedido", {...})` (§4a).

Todo corre en la **sesión del tenant que Fase 3 ya tiene abierta** → el cargo y el registro **commitean
juntos** (invariante "nada mueve cartera sin registro de horas").

### 2.2 DÓNDE se asienta y CÓMO se garantiza la idempotencia

- **Función de `modules/fiados` que se usa**: `FiadosService.crear(...)` (→ `SqlFiadosRepository.crear_fiado`).
  Se usa **tal cual, sin modificarla**.
- **Idempotencia por registro de horas — dos guardas independientes**:
  1. **Servicio (ya existe)**: `crear` toma `lock_cliente` y consulta `fiado_por_key(f"alquiler:horas:{id}")`
     dentro de la sección crítica → una segunda llamada con la misma key devuelve `replay=True` y **no**
     asienta un segundo cargo.
  2. **Base (nueva, dura)**: `UNIQUE(cargos_alquiler.registro_horas_id)`. Aun ante una carrera que
     esquivara el lock, el segundo INSERT viola el UNIQUE.
  - **Hardening recomendado (Ola B)**: índice único parcial
    `CREATE UNIQUE INDEX uq_fiados_idem ON fiados (idempotency_key) WHERE idempotency_key IS NOT NULL`,
    para que la key `alquiler:horas:{id}` sea única también a nivel de fiados (defensa en profundidad;
    hoy solo la protege el lock de cliente).
- **Test-primero (carve-out invariante)**: `asentar_consumo_horas` dos veces con el mismo
  `registro_horas_id` ⇒ el segundo es replay; `clientes.saldo_fiado` sube **una** sola vez; hay **una**
  fila en `cargos_alquiler` y **un** `FiadoMovimiento` cargo con esa key.

### 2.3 Un `Fiado` por consumo vs. un `Fiado` por obra (decisión)

- **v1 recomendado — un `Fiado` por consumo** (`crear` por registro): cero cambios a `modules/fiados`,
  reusa la idempotencia existente, nombra una función real. Costo: muchas filas `Fiado` en obras largas y
  el abono por obra debe repartirse (FIFO) entre los `Fiado` abiertos de la obra (orquestado por nuestro
  service llamando a `FiadosService.abonar` por fiado; los fiados de la obra se listan por `cargos_alquiler`).
- **Alternativa — un `Fiado` por obra + método nuevo `FiadosService.cargar(fiado_id, monto, idempotency_key)`**
  (espejo de `abonar`: inserta `FiadoMovimiento` cargo, sube `fiado.saldo` y `clientes.saldo_fiado`).
  Ventaja: menos filas y abono por obra trivial (un solo fiado). Costo: **modifica `modules/fiados`**
  (coordinar propiedad; en Fase 5 ya no corre el workflow de Fase 1) y requiere mapear obra→fiado
  (columna nullable `fiados.obra_id` + único parcial `WHERE obra_id IS NOT NULL`).
- **Recomendación**: arrancar v1 con "un Fiado por consumo" (menor riesgo, no toca la capa compartida);
  dejar la alternativa documentada por si el volumen de filas molesta.

## 3. Abonos

Se **reutiliza el abono de fiados existente** (`FiadosService.abonar(fiado_id, monto, idempotency_key)` →
`POST /fiados/{fiado_id}/abono`). No se crea abono propio de cartera.

- **Referenciable a la obra**: `cargos_alquiler` mapea obra→fiados, así el dashboard lista los cargos/fiados
  abiertos de una obra y el usuario abona. Abono **a nivel de obra** = repartir el monto FIFO sobre los
  fiados abiertos de esa obra (thin orchestration en `CarteraAlquilerService`, sin SQL suelto: llama a
  `FiadosService.abonar` por fiado).
- El abono ya baja `clientes.saldo_fiado` en la misma transacción → **cierra la colita automáticamente**
  (el motor de cobranza deja de verlo al bajar de `saldo_minimo`, vía `cerrar_al_dia`).
- Open question (§7): si se prefiere abono obra-level directo, añadir `fiados.obra_id` y una variante de
  abono por obra. v1 = FIFO sobre los fiados de la obra.

## 4. Alertas

### 4.a Cupo excedido → aviso al dueño vía SSE (NO bloquea)

Copia exacta del patrón `pack_pagar`: dentro de la sesión del tenant,
`await publish(session, "cartera_cupo_excedido", {"cliente_id", "obra_id", "cupo", "saldo", "excedente", "generado_en"})`.
Transaccional (viaja al COMMIT junto con el cargo). **Decisión del dueño (plan §6a): NO bloquea la
operación** — el registro de horas y el cargo se asientan igual; solo se avisa. El dashboard (TabCartera,
admin) reacciona al evento (badge/toast + fila del cliente en rojo). No se muta la `asignacion` (tabla de
Fase 3): el estado "excedido" se **deriva en vivo** (`saldo > cupo`), evitando tocar el esquema ajeno.

### 4.b Colita estancada → job ARQ diario → ciclo de `pack_cobranza`

- **Detección** (`CarteraAlquilerService.detectar_colitas(ahora, dias_umbral)`): clientes con
  `saldo_fiado > 0` cuyo **último abono** (`fiados_movimientos` tipo `abono`) es de hace **> `dias_colita`**
  (default 15, config) y cuya obra asociada está en estado **`FINALIZADA` o `LIQUIDADA`** (enum `estado_obra`).
  Devuelve `Colita(cliente_id, obra_id, saldo, dias_sin_abono, ultimo_abono_en)`.
- **"Entra al ciclo de recordatorios"**: **no se duplica el envío**. El motor de `pack_cobranza` ya barre a
  todo cliente con `saldo_fiado > saldo_minimo` (`repository.deudores`), así que la colita **ya** está en el
  ciclo cuando `pack_cobranza` está activo (recordatorios + promesas de pago incluidos, tal cual hoy). El
  job **añade**: (1) **aviso interno al dueño** por SSE `publish(session, "cartera_colita", {...})` (patrón
  pagar), y (2) marca la colita para el **semáforo** del dashboard (estado persistido con dedup por
  `cadencia_aviso_dias`, espejo de `PagarAviso`).
- **Job y registro**: función nueva `detectar_colitas_alquiler(ctx)` en `apps/worker/main.py`, registrada en
  `WorkerSettings.cron_jobs` como `cron(detectar_colitas_alquiler, hour={13}, minute={20}, run_at_startup=False)`
  (diario, decalado de los demás; 13:20 UTC ≈ 08:20 a.m. Colombia). Barre `listar_tenants`, filtra
  `"cartera_alquiler" in t.features`, abre `tenant_session`, corre el service con `now_co()` y publica el
  SSE dentro de la sesión (mismo molde que `avisos_pagar`). `try/except` por tenant.
- **Al liquidar la obra**: el saldo pendiente queda visible en la vista de liquidación (endpoint
  `/cartera-alquiler/obras/{obra_id}`) — la colita no "desaparece" al finalizar; al contrario, finalizar es
  lo que la habilita.

## 5. UI — sección en `TabCartera` existente

`dashboard/src/tabs/TabCartera.jsx` es hoy la página de `pack_cobranza` (admin-only, usa
`useFetch` + `useRealtimeEvent`). Se **extiende** (no se crea tab nuevo) con una sección "Cartera de
alquiler", visible cuando la capability `cartera_alquiler` está activa:

- **Tabla de cupos** por cliente: `cupo`, `consumido` (= `saldo_fiado`), `disponible` (= cupo − consumido)
  y **semáforo**: verde (disponible > 20% del cupo), amarillo (0–20%), rojo (excedido) + chip "colita" si el
  cliente está en el set de colitas (obra finalizada/liquidada, sin abono > N días).
- **KPIs** nuevos: cupo total otorgado, consumido total, N colitas, N cupos excedidos.
- Alta/edición de cupo (POST/PUT), desactivación (respeta el único parcial).
- **Tiempo real**: sumar `'cartera_cupo_excedido'` y `'cartera_colita'` al array `EVENTOS` de `TabCartera`
  (ya escucha `fiado_registrado`/`fiado_abonado`, que también mueven la cartera de alquiler).
- Usar skills `ui-ux-pro-max` + `impeccable` (decisión 4 del plan para fases con UI).

## 6. Contrato de API + migración

### 6.1 Router `/cartera-alquiler` (nuevo)

- **Gate**: `dependencies=[Depends(require_feature("cartera_alquiler"))]` (sin flag → 404, patrón pagar).
- **RBAC**: todo `require_role("admin")` (dato sensible de crédito, igual que pagar/cobranza).

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/cartera-alquiler/cupos` | Cupos activos + `consumido`/`disponible`/semáforo por cliente. |
| POST | `/cartera-alquiler/cupos` | Crear cupo (desactiva el activo previo del cliente). |
| PUT | `/cartera-alquiler/cupos/{id}` | Editar cupo/vigencia/activo/notas. |
| GET | `/cartera-alquiler/obras/{obra_id}` | Detalle de cartera de la obra: cargos, saldo, abonos (vista de liquidación). |
| GET | `/cartera-alquiler/colitas` | Colitas detectadas (para el semáforo). |
| GET / PUT | `/cartera-alquiler/config` | `activo`, `dias_colita`, `cadencia_aviso_dias`. |

Los **abonos van por el router de fiados existente** (`POST /fiados/{fiado_id}/abono`), referenciando la
obra desde la UI de cartera. El **consumo (crítico) NO es endpoint HTTP**: es interno, lo dispara Fase 3 al
registrar horas (§2). Acceso a datos solo por `SqlCarteraAlquilerRepository` (regla no negociable #2).

### 6.2 Contrato interno consumido por Fase 3 / Fase 6 (firma pública)

```python
# modules/cartera_alquiler/service.py
@dataclass(frozen=True, slots=True)
class ResultadoConsumo:
    fiado_id: int
    monto: Decimal          # cuantizado a MONEY(12,2)
    saldo_obra: Decimal
    cupo_excedido: bool
    replay: bool            # True si el registro ya había generado su cargo

@dataclass(frozen=True, slots=True)
class Colita:
    cliente_id: int
    obra_id: int
    saldo: Decimal
    dias_sin_abono: int
    ultimo_abono_en: datetime | None

class CarteraAlquilerService:
    def __init__(self, repo: SqlCarteraAlquilerRepository, fiados: FiadosService) -> None: ...

    async def asentar_consumo_horas(
        self, *, registro_horas_id: int, obra_id: int, maquina_id: int,
        asignacion_id: int, cliente_id: int,
        horas_facturables: Decimal, precio_hora: Decimal,
    ) -> ResultadoConsumo: ...
    # idempotente por registro_horas_id; corre en la sesión que le pasa Fase 3.

    async def detectar_colitas(self, *, ahora: datetime, dias_umbral: int) -> list[Colita]: ...
    # cupos CRUD, config y vistas de dashboard
```

**Seam Fase 3 ↔ Fase 5 (coordinar AHORA — este doc es el artefacto de coordinación).** El servicio de
registro de horas de Fase 3, tras insertar el `RegistroHorasMaquina`, si la capability `cartera_alquiler`
está activa y hay asignación activa para `(maquina_id, obra_id)`, llama a
`CarteraAlquilerService.asentar_consumo_horas(...)` **en su misma sesión/transacción**. Import ligero
(`from modules.cartera_alquiler.service import CarteraAlquilerService`), sin evento asíncrono (romper la
atomicidad rompería el invariante). Fase 3 resuelve `asignacion_id`/`precio_hora` por `(maquina_id, obra_id)`
activa (el registro de horas no guarda `asignacion_id`).

### 6.3 Migración (Ola B, DESPUÉS de Fase 3 — NO la escribo)

- **Número**: cabeza actual = `0046_ext_clientes_proveedores`. La migración de cartera sería **`0047_cartera_alquiler`**.
  **Coordinar el número exacto con el workflow de Fase 3 antes de escribirla** (punto de serialización de la
  Ola B: la numeración de migraciones de tenant — plan §5/Ola B). Si Fase 3 introduce una migración en Ola B,
  cartera toma la siguiente.
- **Contenido** (solo `CREATE`/índices, backward-compatible — se aplica vacía a los demás tenants):
  `cupos_alquiler` (+ único parcial `WHERE activo`), `cargos_alquiler` (+ `UNIQUE(registro_horas_id)` + FKs a
  `fiados`/`obras`/`maquinas`/`asignaciones_maquina_obra`/`registros_horas_maquina`), `cartera_config`, y el
  índice de hardening `uq_fiados_idem` sobre `fiados(idempotency_key) WHERE ... IS NOT NULL`.
- `downgrade` limpio (drop en orden inverso). Correr con `tools.migrate_tenants` en dev.
- Si se adopta la alternativa "un Fiado por obra" (§2.3), sumar la columna nullable `fiados.obra_id` (+ único
  parcial) — evaluar en Fase 5, no en v1.

## 7. Preguntas abiertas y riesgos

1. **Conciliación factura ↔ cargos (v1 manual).** Cuando la obra se factura (Fase 7, `FacturaService` desde
   obra), el abono al ledger es **manual** desde el dashboard, referenciado a la obra. `cargos_alquiler` deja
   la traza para una conciliación automática futura (factura→obra→cargos). *Pregunta*: ¿un abono salda cargos
   FIFO o el usuario elige? v1 = FIFO sobre los fiados abiertos de la obra.
2. **Cupo: ¿gobierna crédito total o solo alquiler?** `consumido = clientes.saldo_fiado` mezcla POS-crédito y
   alquiler en un tenant mixto. Para PIM son lo mismo (no hay POS a crédito). *Decisión v1*: `saldo_fiado`
   como consumido (exacto para PIM); el consumo por-obra preciso se deriva de `cargos_alquiler`. Documentar el
   caveat para tenants mixtos.
3. **Frontera de precisión 18,4 → 12,2.** El cargo se calcula en `MONEY4` (horas × precio) y aterriza en el
   ledger `MONEY(12,2)` vía `cuantizar`. Para PIM los montos son pesos enteros (sin pérdida); documentar y
   testear el borde (el plan advierte no mezclar precisiones).
4. **Un `Fiado` por consumo vs. por obra** (§2.3): decisión de volumen de filas vs. tocar `modules/fiados`.
   v1 = por consumo (no toca la capa compartida).
5. **"Flag en la asignación" al exceder cupo** (plan §6a): se **deriva en vivo** (`saldo > cupo`) en vez de
   mutar `asignaciones_maquina_obra` (tabla de Fase 3). Evita acoplar esquemas entre fases. Confirmar que basta
   con derivarlo (no se necesita persistir el flag en la asignación).
6. **Colita y opt-out (Habeas Data).** Un cliente con `opt_out` en cobranza NO recibe recordatorios aunque sea
   colita; el job **igual avisa al dueño** (aviso interno, sin opt-out). Confirmar que es el comportamiento
   deseado (creemos que sí: el dueño debe ver la colita aunque el cliente pidió no ser contactado).
7. **Validación de negocio del cupo**: ¿se puede registrar consumo sin cupo activo? *Decisión v1*: **sí** — el
   cupo solo dispara alerta, no bloquea; sin cupo activo no hay chequeo de excedido (no se avisa). Confirmar
   con el dueño de PIM (plan §7: "cómo llevan hoy la cartera de alquiler" valida este diseño).
8. **Tests test-primero (carve-out)**: (a) idempotencia del consumo (§2.2); (b) aislamiento multi-tenant sobre
   `cupos_alquiler`/`cargos_alquiler` (empresa A no ve la cartera de B); (c) "nada mueve cartera sin registro"
   (no hay camino de consumo fuera del hook de horas). El resto (CRUD de cupos, detección de colita, semáforo)
   va con la cadencia código-primero.
