# Plan — Operación de máquinas en vivo (cronómetro + rotación de operadores)

> Vertical construcción (Construcciones PIM). Extensión **greenfield**: el cronómetro en vivo NO está
> en el spec del cliente (`prospecto-pim/spec-cliente/05_MODULO_INVENTARIO_MAQUINAS.md`), donde toda
> captura de horas es *después del hecho* ("+ Registrar horas de hoy"). Esto profesionaliza la captura
> sin tocar la facturación: la sesión en vivo **se materializa** en el parte de horas diario existente.

## Propósito de negocio

PIM alquila maquinaria pesada; su margen real es 3–4%, así que las horas de máquina deben capturarse
sin fricción y facturarse exactas. Hoy el operador/supervisor digita el parte del día a mano. Este
feature deja que **active la máquina, corra un cronómetro, asigne y rote operadores en vivo**, y al
finalizar el sistema propone las horas medidas para que el supervisor confirme antes de facturar.

## Principio de diseño (no reconstruir la facturación)

La capa en vivo es **captura efímera**; la *verdad facturable* sigue siendo el parte diario
(`RegistroHorasMaquina` + `TurnoHorasMaquina`). Al finalizar, se materializa reusando
`MaquinariaService.registrar_horas` — que ya sabe "agregar turno, recalcular el día y asentar el delta
a la cartera de alquiler" de forma idempotente. Cero cambios en el motor de cobro.

```
ACTIVAR ─→ sesión ABIERTA (iniciada_en = ahora)  ⏱
   └─ tramo operador A (08:00 → …)                 (tramo abierto = finalizado_en NULL)
ROTAR   ─→ cierra tramo A (08:00–12:00) · abre tramo B (12:00 → …)
FINALIZAR (revisión) ─→ cierra tramo B · horas por tramo = tiempo medido (editable)
   └─→ materializa: registrar_horas() por tramo → parte del día → seam cartera (idempotente)
```

Decisiones tomadas con el dueño:
- **Persistencia en servidor** (tablas nuevas, migración 0055): sobrevive a refrescar, tablero
  multi-dispositivo por SSE, auditable.
- **Rotación contigua en v1** (sin pausa): Σ tramos = tiempo activo de la máquina. Un hueco real
  (almuerzo sin operador) se resuelve finalizando y abriendo otra sesión. Pausa/reanudar = fast-follow.
- **Revisar y confirmar** al finalizar: el reloj propone, el supervisor ajusta las horas por tramo, y
  recién ahí se escribe el parte y se factura.

---

## Fase 1 — Backend: modelo de sesión en vivo (migración 0055 + repo + service)

**Migración `0055_operacion_maquina_vivo`** (aplica a todos los tenants vía `tools.migrate_tenants`):
- Enum `estado_sesion_maquina` = `ABIERTA | FINALIZADA | ANULADA`.
- Tabla `sesiones_maquina`: `id`, `maquina_id`→maquinas, `obra_id`→obras, `asignacion_id`→
  asignaciones_maquina_obra (aporta precio/mínimo pactados), `fecha` (Date, día Colombia — clave natural
  del parte), `estado` (default ABIERTA), `iniciada_en`/`finalizada_en` (TIMESTAMPTZ), `registro_horas_id`
  →registros_horas_maquina NULL (se setea al materializar: provenance + ancla anti-doble-facturación),
  `notas`, `creado_en`.
  - **Índice único parcial** `(maquina_id) WHERE estado='ABIERTA'` → una sola sesión abierta por máquina.
- Tabla `tramos_operador`: `id`, `sesion_id`→sesiones_maquina ON DELETE CASCADE, `operador_id`→
  trabajadores NULL, `iniciado_en`/`finalizado_en` (TIMESTAMPTZ; `finalizado_en` NULL = tramo corriendo),
  `horas_confirmadas` Numeric(18,4) NULL (lo confirmado por el humano al finalizar), `creado_en`.
  - **Índice único parcial** `(sesion_id) WHERE finalizado_en IS NULL` → un solo tramo abierto por sesión.
  - Índice por `sesion_id`.

**ORM** (`modules/maquinaria/models.py`): `SesionMaquina`, `TramoOperador` (FKs como BigInteger sin
`relationship`, patrón del repo). Enum mapeado con `create_type=False`.

**Repo** (`modules/maquinaria/repository.py`): `crear_sesion`, `sesion_abierta_de_maquina`,
`obtener_sesion`, `abrir_tramo`, `cerrar_tramo_abierto`, `tramos_de_sesion`, `finalizar_sesion`,
`anular_sesion`, `tablero_operacion(hoy)` (sesiones abiertas + máquinas asignadas-hoy sin sesión, con
nombres de obra/operador, N+1-free). Eventos SSE: `sesion_maquina_iniciada`, `tramo_operador_rotado`,
`sesion_maquina_finalizada`.

**Service** (`modules/maquinaria/operacion_service.py`, nuevo — mantiene funciones <50 líneas):
`OperacionMaquinaService` compone un `MaquinariaService` para materializar.
- `iniciar(maquina_id, obra_id?, operador_id?)`: resuelve la asignación activa que cubre hoy (si hay una
  sola, `obra_id` es opcional); 409 si ya hay sesión abierta de la máquina; 409 si no hay asignación
  activa; crea sesión ABIERTA + primer tramo.
- `rotar(sesion_id, operador_id)`: cierra el tramo abierto (`finalizado_en=now`), abre uno nuevo.
- `finalizar(sesion_id, ajustes?)`: cierra el tramo abierto; horas default por tramo = `(finalizado_en −
  iniciado_en)` en horas (Decimal); aplica overrides de `ajustes`; materializa llamando
  `registrar_horas` una vez por tramo (obra/fecha de la sesión, `horas_trabajadas`=horas del tramo,
  `operador_id`, `hora_inicio/fin` = hora local de los timestamps); guarda `registro_horas_id` en la
  sesión y `horas_confirmadas` por tramo; marca FINALIZADA. **Idempotente**: si ya está FINALIZADA
  devuelve su resumen (replay), sin re-materializar.
- `anular(sesion_id)`: marca ANULADA, cierra tramos, **no** materializa (no factura).
- `tablero()`: arma el tablero en vivo.

**Invariantes críticos (TDD test-primero, carve-out):**
1. Aislamiento multi-tenant: la empresa A nunca ve sesiones de B.
2. Idempotencia de `finalizar`: finalizar dos veces NO crea un segundo parte ni duplica el cargo a
   cartera (ancla: `sesion.registro_horas_id` + idempotencia de `registrar_horas`/turnos).
3. "Nada mueve cartera sin registro de horas": `finalizar` pasa por `registrar_horas`; con cartera
   inyectada asienta exactamente el cargo esperado una vez.
4. Una sesión abierta por máquina y un tramo abierto por sesión (índices únicos parciales → 409).

## Fase 2 — API (router)

`modules/maquinaria/operacion_router.py` (montado con el router de maquinaria; gate `maquinaria`):
- `POST /maquinas/{maquina_id}/operacion/iniciar` → 201 `SesionLeer` (rol vendedor).
- `POST /operacion/{sesion_id}/rotar` `{operador_id}` → `SesionLeer` (vendedor).
- `POST /operacion/{sesion_id}/finalizar` `{ajustes?:[{tramo_id,horas}]}` → `RegistroHorasResultado`
  (el parte materializado) (vendedor).
- `POST /operacion/{sesion_id}/anular` → 200 (admin: deshacer).
- `GET /operacion/tablero` → tablero en vivo (vendedor).
Schemas Pydantic en `modules/maquinaria/schemas.py` (`SesionLeer`, `TramoLeer`, `IniciarOperacion`,
`RotarOperador`, `FinalizarOperacion`, `TableroOperacion`). Errores nuevos en `errors.py`
(`SesionYaAbierta`, `SesionInexistente`, `SesionNoAbierta`) → 409/404.

## Fase 3 — Frontend: pantalla de operación en vivo + cronómetro

Wiring (3 toques, data-driven): `dashboard/src/routes.jsx` (entrada grupo `construccion`, ícono Timer),
`dashboard/src/lib/features.jsx` (`'/operacion': 'maquinaria'`), `dashboard/src/App.jsx` (TABS).

- `dashboard/src/tabs/TabOperacionMaquinas.jsx` — portada: tablero de máquinas en operación + máquinas
  asignadas-hoy disponibles para activar. Refresca con
  `useRealtimeEvent(['sesion_maquina_iniciada','tramo_operador_rotado','sesion_maquina_finalizada','maquina_actualizada','reconnected'], refetch)`.
- `dashboard/src/tabs/construccion/operacion/` (nuevo, espeja `calendario/`/`panel/`):
  - `useCronometro.js` — hook de tiempo transcurrido (setInterval + Date.now() desde `iniciada_en`;
    patrón semilla en `TabHoy.jsx`). **Única pieza net-new.**
  - `TarjetaOperacion.jsx` — tarjeta por máquina: obra, operador actual, cronómetro en vivo, botones
    Rotar / Finalizar; para máquinas asignadas ociosas, botón Activar.
  - `ModalActivar.jsx`, `ModalRotar.jsx`, `ModalFinalizar.jsx` (revisión: horas por tramo editables,
    default = medido; confirma → `finalizar`).
- Reúso: `Semaforo`/`comunes.jsx`, `Turnos.jsx` (desglose de rotación), `FormAsignacionMaquina.jsx`
  (activar exige asignación). Data por `useFetch`.
- Toque de panel: `panel/EstadoMaquinas.jsx` muestra pill "EN OPERACIÓN ⏱" para máquinas con sesión
  abierta (agregar los eventos nuevos a `EVENTOS`/`EVENTOS_CALENDARIO`).
- Limpieza: extraer `construccion/estadoMaquina.js` (el mapa estado→tono/label está duplicado en
  `TabMaquinas.jsx`, `FichaMaquina.jsx`, `panel/EstadoMaquinas.jsx`).

## Fase 4 — Cierre

`/cerrar-fase`: suite backend troceada + evals, frontend (vitest + build), replay vs baseline,
verificación de invariantes críticos, code review y corrección de CRITICAL/HIGH. Migración 0055
`upgrade`/`downgrade` limpia en control y tenant. Ship por PR con CI verde.

## Fuera de alcance (fast-follow)
- Pausa/reanudar (máquina encendida sin operador).
- Cronómetro desde el bot de Telegram (hoy el bot registra el parte cerrado).
- Nuevo valor de enum `origen_registro` = 'OPERACION' (v1 usa la provenance por `sesion.registro_horas_id`).

## Nota de datos (no bloqueante)
Antes de mostrar esto al cliente, PIM debe quedar con datos reales: existe `tools/vaciar_datos_tenant.py`
(pendiente, sin commitear) para vaciar los datos demo del tenant conservando login/config/migraciones.
