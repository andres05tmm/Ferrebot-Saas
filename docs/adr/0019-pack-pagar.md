# ADR 0019 — `pack_pagar`: avisos de cuentas por pagar al dueño (aviso interno)

- **Estado:** aceptado (16 jun 2026)
- **Contexto:** `docs/adr/0015-pack-cobranza.md` (el patrón a espejar), `modules/proveedores/`
  (`facturas_proveedores` ya existe), `docs/whatsapp-agentes-arquitectura.md` (runtime + packs).

## Contexto

Los negocios del nicho no solo cobran: también **deben**. La ferretería le compra a sus proveedores a
crédito (`facturas_proveedores` con `pendiente > 0`), y olvidar una factura que vence cuesta recargos,
cupo y relación con el proveedor. Hoy ese seguimiento es manual y se aplaza igual que la cobranza.

`pack_pagar` es el **espejo de `pack_cobranza` al otro lado de la cartera**: cobranza le recuerda al
DEUDOR lo que te debe; pagar le avisa al DUEÑO lo que ÉL debe y está por vencer o vencido.

**Diferencia clave que lo hace más simple que cobranza:** es un **AVISO INTERNO al dueño**, no un
agente de cara a un tercero. El dueño es quien recibe el mensaje; nadie externo lo lee. Por lo tanto
`pack_pagar` **NO lleva**:

- **opt-out / Habeas Data:** el destinatario es el propio dueño, no un cliente con derecho a no ser
  contactado.
- **promesas de pago:** no hay nadie a quien pedirle una promesa; el dueño decide y paga por el POS.
- **guardarraíles de tono ni herramientas de cara a un cliente:** no hay conversación con un tercero.

Se reduce, entonces, a **leer las cuentas por pagar y decidir de forma determinista cuáles ameritan
aviso**. El motor nunca calcula saldos: `pendiente` lo mantiene el flujo de abonos existente
(`modules/proveedores`, regla #7: nada toca saldos sin movimiento; este pack solo LEE).

## Decisión

### Alcance por fases

- **Fase 1 (este ADR / tenant 0026):** datos + motor determinista. Migración, `modules/pagar/`
  (models · repository · schemas · errors · service) y el motor. **Sin** worker/cron, **sin** flag en
  el catálogo, **sin** router ni dashboard.
- **Fase 2:** cableado — cron del worker ARQ (`avisos_pagar`), flag `pack_pagar` en el catálogo, y
  router + página de dashboard ("Cuentas por pagar"). El callback `enviar` que hoy se inyecta lo
  proveerá el worker con el canal del dueño.

### Capa 1 — Datos (migración tenant `0026_pack_pagar`)

- **`facturas_proveedores.fecha_vencimiento`** (DATE, nullable): vencimiento de la cuenta. Se agrega a
  la tabla existente; segura sobre datos actuales (queda NULL y el motor deriva el vencimiento).
- **`pagar_config`** (una fila por tenant, get-or-create con defaults):
  `activo` (true), `dias_aviso_previo` (3 → avisar N días antes de vencer; 0 = solo al vencer),
  `cadencia_dias` (3 → no repetir el aviso de la misma factura antes de N días),
  `hora_inicio` / `hora_fin` (ventana, 08:00–18:00), `plazo_default_dias`
  (30 → vencimiento derivado de `fecha` cuando `fecha_vencimiento` es NULL).
- **`pagar_avisos`** (estado de dedup por factura): `factura_id` UNIQUE (FK → `facturas_proveedores`,
  `ON DELETE CASCADE`), `avisos_enviados`, `ultimo_aviso_en`. No se crea tabla de deuda nueva: la
  fuente de verdad sigue siendo `facturas_proveedores`.

### Capa 2 — Motor (`modules/pagar/service.py`, determinista)

`clasificar_cuenta(factura, hoy, …)` — **función pura**: vencimiento efectivo = `fecha_vencimiento`, o
`fecha + plazo_default_dias` si es NULL; marca `por_vencer` (vence en ≤ `dias_aviso_previo`, aún no
vencida) y `vencida` (vencimiento < hoy). Mutuamente excluyentes.

`cuentas_por_pagar(hoy)` — todas las facturas con `pendiente > 0`, clasificadas (alimenta el dashboard
de Fase 2 y los tests).

`procesar_avisos(ahora, enviar)` — corrida del cron sobre la base del tenant (espejo de
`procesar_recordatorios` de cobranza):

1. Si la config está **inactiva** → no hace nada.
2. **Ventana horaria:** fuera de `[hora_inicio, hora_fin)` no se envía nada.
3. Por factura con saldo: amerita aviso si está `por_vencer` o `vencida`; la **cadencia**
   (`cadencia_dias` desde el último aviso de ESA factura) evita repetir.
4. Se arma **UN** resumen para el dueño con las facturas elegibles (totales por vencer / vencidos);
   `enviar(aviso)` lo provee el worker (Fase 2). **Solo un envío exitoso sella el dedup** de todas las
   facturas incluidas — mismo seam que cobranza: un fallo de red se reintenta en la próxima corrida sin
   perder ni duplicar el aviso.

### Lo que NO lleva (por ser aviso interno)

- Sin opt-out, sin promesas de pago, sin herramientas de cara a un tercero, sin guardarraíles de tono.
- El motor solo **lee** `facturas_proveedores`; jamás escribe `pendiente`/`pagado`/`estado` (eso es del
  flujo de abonos de `modules/proveedores`, con su recálculo).

### Cableado (Fase 2)

- Flag `pack_pagar` en el catálogo, **requiere `proveedores`** (las cuentas por pagar viven ahí).
- Cron del worker ARQ `avisos_pagar` (la ventana horaria la aplica el motor), barrido multi-tenant
  gateado por capacidad — espejo de `recordatorios_cobranza`.
- Router `/api/v1/pagar` (cuentas por pagar, config) + página "Cuentas por pagar" — gateado por flag,
  RBAC admin (el dueño).

## Alternativas consideradas

- **Reusar `pack_cobranza`:** descartado — cobranza es un agente de cara al deudor (opt-out, promesas,
  tono); pagar es un aviso interno. Forzar el mismo plano arrastraría conceptos que aquí no aplican.
- **`fecha_vencimiento` obligatoria (NOT NULL):** descartada — las facturas existentes de Punto Rojo no
  la tienen. Se deja nullable y el motor deriva el vencimiento de `fecha + plazo_default_dias`.
- **Un aviso por factura (N mensajes):** descartado — sería ruido para el dueño. Se envía UN resumen
  por corrida; la cadencia es por factura para que el resumen no repita lo ya avisado.
- **Que el motor mueva saldos / marque pagadas:** descartado por la regla #7 — el saldo solo cambia con
  un abono por el flujo existente; este pack solo lee.

## Consecuencias

- Punto Rojo (y cualquier tenant con `proveedores`) podrá prender `pack_pagar` y dejar de olvidar
  vencimientos, sin vender nada nuevo: ROI medible en recargos evitados.
- `pack_pagar` cumple la métrica M-producto: no toca el runtime del agente (datos + motor + flag +
  dashboard); el aviso es interno, así que ni siquiera consume plantillas pagas de cara a terceros.
- Cuando un vertical sin POS de compras necesite cuentas por pagar, se generaliza el origen detrás del
  mismo motor (el contrato de `clasificar_cuenta` no cambia).
