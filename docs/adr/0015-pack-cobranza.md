# ADR 0015 — `pack_cobranza`: recordatorios de cartera por WhatsApp (tono amable)

- **Estado:** aceptado (11 jun 2026)
- **Contexto:** `docs/plan-impulso-agentes-2026.md` §2.4 (quick win de la Ola 1),
  `docs/whatsapp-agentes-arquitectura.md` (runtime + packs), `docs/pack-agenda-citas.md` (el patrón).

## Contexto

Los negocios del nicho tienen cartera: fiados de la ferretería, cuotas de la clínica, mensualidades
del gym. Hoy cobrar es una tarea manual e incómoda que se aplaza. Punto Rojo (tenant #1) ya tiene
fiados reales (`fiados` + `fiados_movimientos` + `clientes.saldo_fiado`), así que este pack se prueba
sin vender nada nuevo y su ROI se mide en pesos recuperados.

El patrón de pack ya está probado con `pack_agenda`: **tablas en la DB del tenant + motor determinista
+ herramientas function-calling acotadas al teléfono que escribe + flag**. El agente nunca calcula
saldos; entiende la intención y llama herramientas.

## Decisión

### Capa 1 — Datos (migración tenant `0017_cobranza`)

- **`cobranza_config`** (una fila por tenant, get-or-create con defaults):
  `activo` (default true), `cadencia_dias` (7), `max_recordatorios` (3), `hora_inicio` (09:00),
  `hora_fin` (19:00), `saldo_minimo` (0 → todo saldo > 0 se recuerda).
- **`cobranza_clientes`** (estado de cobranza por cliente, get-or-create):
  `cliente_id` UNIQUE, `opt_out` (Habeas Data), `recordatorios_enviados`, `ultimo_recordatorio_en`.
- **`promesas_pago`**: `cliente_id`, `telefono`, `fecha_promesa`, `estado`
  (`vigente → cumplida | incumplida | reemplazada`).
- **`pagos_reportados`**: `cliente_id`, `telefono`, `nota`, `verificado` — la bandeja "por verificar"
  del dashboard cuando el cliente dice "ya pagué".

**No se crea una tabla de deuda nueva:** la fuente de verdad sigue siendo `fiados_movimientos` y el
contador `clientes.saldo_fiado` (regla #7: nada toca saldos sin movimiento; este pack NO toca saldos,
solo los lee).

### Capa 2 — Motor (`modules/cobranza/service.py`, determinista)

`procesar_recordatorios(ahora, enviar)` — corrida del cron sobre la base del tenant:

1. **Cierre de ciclo:** clientes con recordatorios abiertos cuyo saldo llegó a 0 → contador a 0 y su
   promesa vigente pasa a `cumplida`.
2. **Ventana horaria:** fuera de `[hora_inicio, hora_fin)` no se envía nada (respeto al cliente final).
3. Por cada deudor (saldo > `saldo_minimo`, con teléfono): se salta si `opt_out`; si tiene **promesa
   vigente** no vencida se pausa (la promesa compra silencio); si la promesa venció con deuda → pasa a
   `incumplida` y se reanuda; **cadencia** (`cadencia_dias` desde el último envío) y **tope**
   (`max_recordatorios` por ciclo de deuda) limitan los envíos.
4. `enviar(deudor)` lo provee el worker (plantilla paga de WhatsApp vía Kapso); solo si devuelve True
   se sella el envío (mismo seam que la reconfirmación de agenda: fallo de red → se reintenta luego).

La identidad cliente↔WhatsApp es el **teléfono** (`clientes.telefono`), comparado por los últimos 10
dígitos (normaliza `300 123 4567` vs `573001234567`).

### Capa 3 — Herramientas del agente (`ai/cobranza_tools.py`, cara al deudor)

Gateadas por el flag `pack_cobranza`; el teléfono viaja SOLO en el `Contexto` del canal (el modelo no
puede consultar deudas ajenas — guardarraíl idéntico a agenda):

| Herramienta | Hace |
|---|---|
| `mi_saldo` | saldo actual + promesa vigente del que escribe (solo lectura) |
| `prometer_pago(fecha)` | registra promesa (futura, ≤ 30 días); reemplaza la vigente |
| `reportar_pago(detalle)` | registra el reporte **y escala a la bandeja humana** (verificación del comprobante) |
| `no_mas_recordatorios` | opt-out (Habeas Data); el motor deja de recordarle |

`escalar_humano` ya es de núcleo. **El agente jamás calcula ni negocia el saldo** (solo el motor lee
`saldo_fiado`); jamás registra abonos (eso es del POS, con su movimiento de caja).

### Guardarraíles (no configurables)

- **Tono respetuoso fijado por sistema:** la sección de cobranza del system prompt es fija (amable,
  sin presión ni amenazas); la `persona` del negocio no puede volverla agresiva.
- Tope de recordatorios + cadencia + ventana horaria + opt-out en el **motor** (no dependen del LLM).
- Habeas Data: el agente solo conoce nombre + teléfono + saldo del que escribe; nada más.

### Cableado

- Flag `pack_cobranza` en el catálogo, **requiere `fiados`** (la cartera v1 ES el saldo de fiados).
- Cron del worker ARQ `recordatorios_cobranza` cada 30 min (la ventana horaria la aplica el motor),
  barrido multi-tenant gateado por capacidad — espejo de `reconfirmaciones_agenda`.
- Plantilla paga: `kapso_template_cobranza` (+ idioma) en settings; vacía = el cron no envía
  (motor inactivo hasta aprobar la plantilla en la WABA) — mismo contrato que la reconfirmación.
- Dashboard (página "Cartera" del plan): router `/api/v1/cobranza` (deudores, config, promesas,
  pagos reportados por verificar, opt-out) — gateado por flag, RBAC admin.

## Alternativas consideradas

- **Tabla `cuentas_por_cobrar` genérica** (cuotas de clínica, mensualidades): pospuesta. v1 cobra
  sobre fiados (lo que Punto Rojo tiene HOY). Cuando un vertical sin POS necesite cartera, se
  generaliza el origen del saldo detrás del mismo motor (el contrato de las herramientas no cambia).
- **Recordatorio con monto en la plantilla:** las plantillas de WhatsApp con variables requieren
  aprobación específica; v1 usa plantilla genérica ("tienes un saldo pendiente, escríbenos") y el
  saldo exacto lo da `mi_saldo` cuando el cliente responde (ya dentro de la ventana de 24h, gratis).
- **Cobranza automática agresiva (escalamiento de tono):** descartada por diseño — el tono es fijo.

## Consecuencias

- Punto Rojo puede prender `pack_cobranza` con sus fiados reales: primer agente con ROI medible en pesos.
- Mensajes de plantilla son pagos → medirlos por tenant (cuota en planes) queda anotado para billing.
- `pack_cobranza` cumple la métrica M-producto del plan: no toca el runtime (datos + motor +
  herramientas + flag); el único cambio en `apps/wa/agent.py` es el wiring declarativo del pack.
