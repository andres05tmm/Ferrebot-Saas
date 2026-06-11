# ADR 0013 — Frente de pagos: Bre-B vía PSP (link/QR con webhook)

- **Estado:** aceptado — infraestructura v1 implementada (11 jun 2026): puerto `core/pagos` +
  adaptador Bold + tabla `cobros` (0021) + conciliación por polling + flag `pagos_online` +
  integración con pedidos. Pendiente con la cuenta Bold real: verificar tarifa del QR Bre-B online
  y la spec del webhook (v1.1 — mientras tanto el polling concilia).
- **Contexto:** `docs/plan-impulso-agentes-2026.md` (Ola 2 §8): habilitar **anticipos** de agenda y
  reservas de hotel (`agenda_config.requiere_anticipo` ya diseñado) y **cobro en pedidos**
  (`pack_pedidos`). Pendiente #5 del handoff 10-jun (research de PSPs).

## Contexto: qué cambió en Colombia

**Bre-B** (sistema de pagos inmediatos interoperado del Banco de la República) está vivo desde
octubre 2025 y masificado: >5 millones de operaciones diarias (may-2026), ~99 millones de llaves de
~34 millones de clientes, de los cuales ~2,9 millones son comercios. Pagar con llave o QR es
**instantáneo, 24/7 e interoperable** entre todos los bancos y billeteras (Nequi, Daviplata, etc.).
QR con monto predeterminado disponible para comercios desde el arranque.

**La trampa para nosotros no es cobrar: es CONFIRMAR.** Un negocio puede recibir Bre-B gratis hoy
con su llave/QR del banco, pero el dinero entra a su cuenta **sin notificación programática**: no
hay webhook del Banco de la República hacia el software del comercio. Y todo lo que el motor de
FerreBot necesita es el evento de confirmación (anticipo pagado → cita confirmada; pedido pagado →
a cocina) con idempotencia. Sin webhook, la "confirmación" es el cliente mandando un pantallazo —
exactamente la bandeja humana de `reportar_pago` que ya tenemos en cobranza.

## Opciones evaluadas

| | A) Bre-B directo (llave del negocio) | B) **Bold** | C) Wompi (Bancolombia) |
|---|---|---|---|
| Costo por cobro | **$0** | ~1,5% (QR; verificar tarifa exacta del QR Bre-B online) | 2,65% + $700 + IVA (agregador) |
| Bre-B | nativo | **QR Bre-B ya integrado en link de pago y botón** | aún NO listado como método (jun-2026) |
| Confirmación programática | ❌ ninguna (conciliación manual) | ✅ webhook + `GET /online/link/v1/{id}` (estados `ACTIVE → PROCESSING → PAID / REJECTED / CANCELLED / EXPIRED`) | ✅ webhook + API de transacciones |
| Otros métodos | — | tarjetas, PSE, Nequi, botón | tarjetas, PSE, Nequi, botón Bancolombia, efectivo corresponsal, BNPL |
| API | — | `POST /online/link/v1` con header `x-api-key`; `reference` único (≤60 chars) = nuestra llave de idempotencia; `expiration_date`; `callback_url` | API REST completa, sandbox maduro, acceptance tokens |
| Disponibilidad de la plata | inmediata | en segundos a la Bold Account | día hábil siguiente |
| Requisito de entrada | cuenta bancaria con llave | Bold Account (sin volumen mínimo) | cuenta Bancolombia; plan Gateway exige ≥2.000 tx/mes |

## Decisión propuesta

1. **Puerto `PagosPort` propio, PSP detrás.** El dominio solo conoce un concepto: **solicitud de
   cobro** (`crear_link(monto, referencia, vence) → url` + webhook de confirmación → estado
   `pendiente → pagado | vencido | cancelado`). El PSP es un adaptador — mismo patrón que
   `CalendarPort` (gcal) y el cliente MATIAS. Credenciales del PSP **por tenant, cifradas en el
   control DB** (cada negocio cobra a SU cuenta; jamás un fondo común nuestro).
2. **Adaptador v1: Bold.** Único PSP con QR Bre-B en pagos online + webhook hoy, API mínima
   (una API key, un endpoint, `reference` idempotente), sin volumen mínimo y con la plata en
   segundos — exactamente el perfil de nuestros tenants (mipymes que viven en WhatsApp). El agente
   manda el link/QR por el chat; el webhook confirma; el motor actúa.
3. **Wompi queda como segundo adaptador** (no v1): gana si el tenant ya es Bancolombia-céntrico o
   necesita efectivo en corresponsales/BNPL, pero hoy no lista Bre-B y su agregador es más caro.
4. **El modo `manual` no muere:** sigue siendo el fallback universal (etiqueta "pendiente de pago" +
   comprobante a bandeja humana), y el único modo para tenants sin PSP.
5. **Webhook → mismo patrón MATIAS:** endpoint con token por tenant, registro idempotente
   (`reference`), job ARQ que aplica el cambio de estado sobre la base del tenant y emite SSE.

## Qué habilita (en orden del plan)

- **Anticipos de agenda/reservas:** `requiere_anticipo=true` → el agente agenda en `pendiente`,
  manda el link, el webhook la confirma (o vence y libera el cupo).
- **Cobro en `pack_pedidos`:** pedido confirmado → link → `pagado` → a preparación.
- **Cobranza:** el recordatorio puede incluir el link de pago del saldo (cierra el ciclo completo:
  recordar → cobrar → conciliar solo).

## Por verificar antes de implementar (con la cuenta abierta)

- Tarifa exacta del QR Bre-B en pagos online de Bold (la pública del QR presencial es ~1,5% con
  4×1000 incluido; la online puede diferir) y si hay retención por reversos.
- Detalle del webhook de Bold: firma/secret, formato de eventos y política de reintentos (la doc
  pública no lo especifica — pedir la spec o probar en sandbox).
- Si Wompi habilita Bre-B (anunciado en el ecosistema para 2026), reevaluar el adaptador v1 vs 2.

## Consecuencias

- El frente de pagos NO arranca como pack: es infraestructura transversal (como facturación) que
  los packs consumen. Implementarlo solo cuando un caso real lo pida (anticipo de la clínica/hotel
  demo o el primer pedido), nunca antes — regla anti-dispersión del plan.
- Comisión del PSP es costo del tenant (transparente en su Bold/Wompi), no nuestro; nuestros planes
  no tocan el flujo de plata (evita ser agregador regulado).

### Fuentes

- [Bre-B — Banco de la República](https://www.banrep.gov.co/es/bre-b) · [qué es](https://www.banrep.gov.co/es/bre-b/que-es) · [documento técnico feb-2026](https://www.banrep.gov.co/en/publications-research/working-papers/paper-bre-b-february-2026)
- [Bre-B supera 5M de operaciones diarias — Infobae may-2026](https://www.infobae.com/colombia/2026/05/06/bre-b-disparo-sus-transacciones-en-colombia-y-ya-mueve-hasta-5-millones-de-operaciones-diarias/) · [Semana: $105 billones en 6 meses](https://www.semana.com/economia/capsulas/articulo/bre-b-moviliza-mas-de-105-billones-en-seis-meses-y-acelera-la-adopcion-de-pagos-inmediatos-en-colombia/202659/)
- [Bold — QR Bre-B en pagos online](https://bold.co/academia/resena-de-productos/bre-b-llega-a-tus-pagos-en-linea-ahora-tus-clientes-pueden-pagar-con-qr-en) · [ayuda: cómo pagan con QR Bre-B](https://ayuda.bold.co/cmo-pueden-mis-clientes-pagar-con-qr-bre-b-en-mi-tienda-virtual-o-link-de-pago-HJT59bePWe) · [API link de pagos](https://developers.bold.co/pagos-en-linea/api-link-de-pagos) · [tarifas](https://bold.co/tarifas)
- [Wompi — métodos de pago](https://docs.wompi.co/en/docs/colombia/metodos-de-pago/) · [planes y tarifas](https://wompi.com/es/co/planes-tarifas/) · [links de pago](https://docs.wompi.co/en/docs/colombia/links-de-pago/)
