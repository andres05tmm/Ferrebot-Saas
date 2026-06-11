# Verificación del plan de impulso — qué quedó listo y qué necesita a Andrés

> Generado el 11-12 jun 2026 al cierre de la ejecución completa de `plan-impulso-agentes-2026.md`.
> **Propósito:** la checklist de la próxima sesión — ir comprobando una a una cada implementación.
> Todo lo de abajo está en `main` con CI verde y suite completa pasando; "verificado" aquí significa
> *probado end-to-end con servicios reales*, que es lo que falta.

---

## 1. Qué se completó (todo en `main`)

| # | Entrega | PR | Migración | Flag |
|---|---|---|---|---|
| 1 | `pack_cobranza` (ADR 0015): motor + herramientas + cron | #17 | tenant 0017 | `pack_cobranza` (req. `fiados`) |
| 2 | Página **Cartera** (`/cartera`) + eventos SSE | #18 | — | ídem |
| 3 | Métrica **pesos recuperados** (log + endpoint + KPI) | #19 | tenant 0018 | ídem |
| 4 | ADR 0013 **pagos** (research Bre-B/Bold/Wompi) | #20 | — | — |
| 5 | `pack_pedidos` (ADR 0016): motor + herramientas + **kanban** (`/pedidos`) | #21 | tenant 0019 | `pack_pedidos` (req. `pos`) |
| 6 | `pack_ventas` (ADR 0017): cotizaciones + carrito por WhatsApp | #22 | tenant 0020 | `pack_ventas` (req. `pos`) |
| 7 | **Frente de pagos**: `PagosPort` + adaptador **Bold** + conciliación + link en pedidos | #23 | tenant 0021 | `pagos_online` |
| 8 | `pack_reservas`: variante noches del motor de agenda + anticipo | #24 | tenant 0022 | `pack_reservas` (req. `pack_agenda`) |
| 9 | `pack_postventa`: encuesta 1-5 + reseñas + cron | #25 | tenant 0023 | `pack_postventa` |
| 10 | **Reporte del agente** (`GET /api/v1/agente/reporte`) | #25 | — | req. `canal_whatsapp` |

Además (sesión anterior, ya verificado en prod): migraciones 0017/0018 aplicadas en `puntorojo` y
`clinica-demo`; el pre-deploy de Railway (`migrate_tenants`) aplica las 0019–0023 solas en el
próximo deploy. El system prompt del agente WhatsApp ahora **se compone por capacidades** (cada
pack agrega su sección; sin `pack_agenda` la intro es genérica).

**Métrica M-producto del plan: cumplida.** Ningún pack tocó el runtime del agente más allá del
wiring declarativo (deps + despacho + sección de prompt).

---

## 2. Cómo verificar cada cosa (checklist de la próxima sesión)

> Prerrequisito común: deploy a Railway hecho (las migraciones corren solas en el pre-deploy) y el
> flag del pack prendido en el tenant de prueba: `python -m tools.set_feature <slug> <flag> on`
> (vía `railway ssh` al Worker) o el panel `/admin`.

### 2.1 Cobranza (Punto Rojo, fiados reales)
- [ ] Prender `pack_cobranza` → la pestaña **Cartera** aparece (admin) con los deudores reales.
- [ ] WhatsApp del deudor: "¿cuánto debo?" → `mi_saldo` responde el saldo real (jamás otro cliente).
- [ ] "Te pago el viernes" → promesa registrada (visible en Cartera); el cron no le escribe hasta esa fecha.
- [ ] "Ya pagué" → cae a **Pagos por verificar** + conversación escalada a humano.
- [ ] "No me escriban más" → opt-out (campana tachada en Cartera).
- [ ] (Con plantilla WABA aprobada) el cron envía el recordatorio respetando ventana 09–19, cadencia y tope.
- [ ] KPI **Recuperado** sube cuando un deudor recordado abona en el POS.

### 2.2 Pedidos (restaurante demo o ferretería con domicilio)
- [ ] Prender `pack_pedidos` → pestaña **Pedidos** (kanban) visible.
- [ ] WhatsApp: "me mandas 2 hamburguesas y una coca-cola" → `armar_pedido` resuelve contra el catálogo
      real (probar typo: "hamburguza" → sugerencia, no invento).
- [ ] Dar dirección + barrio + método de pago → confirmado con tarifa de la zona (o default) y
      tiempo estimado; el pedido APARECE EN VIVO en el kanban (SSE).
- [ ] Avanzar columnas (A cocina → Despachar → Entregado); "¿cómo va mi pedido?" responde el estado.
- [ ] Fuera del horario de cocina → el agente lo dice y no arma nada. Stock insuficiente → ofrece ajustar.
- [ ] Verificar que el inventario NO cambió (el pedido no descuenta stock).

### 2.3 Cotizaciones (ferretería)
- [ ] Prender `pack_ventas`. WhatsApp: "¿a cómo el bulto de cemento?" → precio real del catálogo.
- [ ] "¿Y si llevo 10?" → aplica el precio escalonado real (verificar contra el producto).
- [ ] Armar carrito ("agrégame 10 bultos y 3 láminas de drywall") → emitir → resumen con vigencia.
- [ ] `mostrar_stock=false` en config → el agente no revela existencias.
- [ ] Dashboard: `GET /api/v1/cotizaciones` lista; marcar aceptada/cancelada funciona (UI React pendiente — §4).

### 2.4 Pagos (requiere cuenta Bold — §3)
- [ ] Sembrar `secretos_empresa.bold_api_key` (cifrada) + flag `pagos_online`.
- [ ] Confirmar un pedido por WhatsApp → el agente manda el LINK de pago real de Bold.
- [ ] Pagar con QR Bre-B desde un banco → en ≤5 min el cron `conciliar_cobros` lo marca `pagado` (+SSE).
- [ ] Sin llave Bold: el cobro queda `manual` y el negocio lo cierra desde `/api/v1/pagos/cobros/{id}/pagado-manual`.
- [ ] **Verificar la tarifa real del QR Bre-B online de Bold y la spec del webhook** (para pasar de polling a webhook, v1.1).

### 2.5 Reservas (hotel demo — propuesta Brisa)
- [ ] Prender `pack_agenda` + `pack_reservas`; crear servicio "Noche estándar" con precio + recursos
      tipo `habitacion` que lo prestan; configurar `requiere_anticipo` + `anticipo_tipo/valor` si aplica.
- [ ] WhatsApp: "¿tienen habitación para el viernes, 3 noches?" → lista real con tarifa y total.
- [ ] Reservar → con anticipo queda `pendiente` (+ link de pago si hay Bold); sin anticipo, confirmada.
- [ ] Doble reserva del mismo rango → el segundo cliente recibe "ya no está libre".
- [ ] "Mis reservas" / cancelar → funcionan con las herramientas de agenda de siempre.

### 2.6 Postventa
- [ ] Prender `pack_postventa`; configurar `google_maps_url` del negocio.
- [ ] (Con plantilla WABA) cumplir una cita / entregar un pedido → a las N horas llega la encuesta.
- [ ] Responder "5" → registrado + el agente invita a la reseña con el link; responder "1" → disculpa + ofrece humano.
- [ ] `GET /api/v1/postventa/satisfaccion` refleja el promedio.

### 2.7 Reporte del agente
- [ ] `GET /api/v1/agente/reporte` (admin): bloques presentes según los packs del tenant; los números
      cuadran con lo hecho en las pruebas anteriores (citas, % sin humano, pedidos, recuperado, satisfacción).

---

## 3. Lo que SOLO Andrés puede hacer (bloqueos operativos)

1. **Plantillas WABA en Kapso** (mensajes pagos iniciados por el negocio) — sin ellas los crons quedan
   inactivos por diseño (el dedup no se sella y reintenta cuando existan):
   - `KAPSO_TEMPLATE_RECORDATORIO` (reconfirmación de citas — ya existía).
   - `KAPSO_TEMPLATE_COBRANZA` (recordatorio de cartera, genérica sin monto).
   - `KAPSO_TEMPLATE_POSTVENTA` ("¿cómo te fue? califícanos del 1 al 5").
2. **Cuenta Bold** → llave de identidad por tenant (se siembra cifrada como `bold_api_key`) +
   verificar tarifa QR Bre-B online + pedir la spec del webhook.
3. **Prender flags** por tenant (orden sugerido: `pack_cobranza` en Punto Rojo primero — ROI inmediato).
4. **Número de WhatsApp (Kapso) para clinica-demo** → habilita la demo vendible de la Ola 1.
5. **Elegir la propuesta de diseño** (Aurora / Brisa / Lienzo en `design-propuestas/`) → desbloquea
   portarla como tema base del dashboard React y la landing.
6. Deploy a Railway (push a main ya quedó; redeploy aplica migraciones 0019–0023 solas).

## 4. Pendientes de código (conscientes, NO bloqueantes)

- **UI React de cotizaciones** (lista/marcado): el backend completo existe; el plan §4 no definía esta
  página. Decidir si va en Cartera, en una pestaña propia o dentro de Ventas.
- **UI del reporte del agente**: el endpoint existe; falta la página "Reportes" del modelo de páginas
  (plan §4.8) consumiéndolo.
- **Landing pública (C1) y billing (C2)**: gateados por la decisión de diseño (#5 de arriba); las
  propuestas navegables ya existen para vender mientras tanto.
- **Webhook de Bold** (v1.1): hoy concilia por polling cada 5 min — correcto y suficiente; el webhook
  baja la latencia cuando Bold entregue la spec.
- **Pedido → venta POS** (descuenta stock, mueve caja, emite POS electrónico) y **cotización →
  venta**: v2 documentadas en los ADRs 0016/0017.
- **Vista SEMANA del calendario de agenda** y UI de `google_calendar_id` (pendientes previos).
- **pack_faq RAG v2** (pgvector): el v1 keyword ya opera.

## 5. Cómo correr la verificación local (sin tocar prod)

```bash
# servicios
docker start ferrebot-pg ferrebot-redis
# suite completa (incluye los ~60 tests nuevos de los packs)
.venv/Scripts/python -m pytest -q
# dashboard
cd dashboard && npm test -- --run
# API + worker (dos terminales)
uvicorn apps.api.main:app --reload --port 8000
arq apps.worker.main.WorkerSettings
```
