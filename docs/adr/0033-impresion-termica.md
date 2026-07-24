# ADR 0033 — Impresión térmica: cola en backend + agente local suscrito

- **Estado:** PROPUESTO — checkpoint R0 con Andrés (goal Restaurante Ronda 2)
- **Fecha:** 2026-07-24
- **Relacionados:** ADR 0032 (pack restaurante; D5 zonas de comandas, D7 propina), ADR 0016
  (`pack_pedidos`), ADR 0007 (manifiesto), `docs/goal-restaurante-ronda2.md` §A.2–A.3
- **Investigación:** `docs/research/comparativa-yuumi-post-pack-restaurante.md` (Yuumi resuelve
  esto con un plugin local que imprime "sin diálogos de confirmación")

## Contexto

Sin papel no hay restaurante: la comanda impresa en cocina y la precuenta en mesa son rito
operativo en Colombia. Las impresoras térmicas (ESC/POS, 80/58mm) son locales (USB/red LAN) y el
backend vive en Railway: hay que cruzar ese puente sin pedirle al navegador permiso en cada ticket.

Dos patrones de industria:

- **(a) Puente navegador→localhost** (QZ Tray, plugin Parzibyte): la página del dashboard manda el
  trabajo por HTTP a un agente local. Simple, pero **solo imprime si hay un navegador abierto con
  el dashboard en esa máquina** — un pedido de WhatsApp a las 7pm no imprime su comanda si nadie
  tiene la pestaña abierta.
- **(b) Agente suscrito al backend:** un servicio local por sede se conecta SALIENTE al backend,
  recibe trabajos de una cola de impresión y los ejecuta en ESC/POS. Imprime sin navegador, sin
  puertos abiertos ni firewall (la conexión nace adentro), y reusa la infraestructura de eventos
  existente (`core/events/publisher`). Es lo que el plugin de Yuumi aparenta ser por fuera.

## D1 — Patrón: (b) agente suscrito, con (c) fallback navegador

**Decisión:** patrón (b) como camino principal; **(c) impresión por navegador**
(`window.print` + CSS de 80/58mm) como fallback para quien aún no instala el agente. El fallback
cubre precuenta/comanda/comprobante desde el dashboard con una vista de impresión térmica; el
agente cubre además el caso crítico sin navegador (comanda de WhatsApp directa a cocina).

Descartado (a) como principal: condena la comanda automática al azar de una pestaña abierta.

## D2 — Cola de impresión (tabla tenant `trabajos_impresion`)

Migración tenant **aditiva** (tabla nueva; los demás verticales no ven cambio — tabla vacía no
cuesta):

- `tipo` (`comanda` | `precuenta` | `comprobante`), `payload JSONB` **determinista** (datos del
  ticket ya resueltos: ítems, modificadores, totales, branding — el agente NO consulta negocio),
  `zona_id FK NULL` (comandas; ADR 0032 D5), `ancho` (80|58, del perfil de impresora),
  `estado` (`pendiente → entregado_agente → impreso | error`), `intentos`, `error_detalle`,
  referencia al origen (`pedido_id`/`venta_id`/`comanda_id` NULL-safe) y timestamps por transición.
- **`idempotency_key` UNIQUE** — guardarraíl central: *una comanda jamás se imprime dos veces por
  un reintento*. La clave es determinista por origen (p. ej. `comanda:{comanda_id}:v1`); el
  reintento de confirmación del pedido, el doble clic y el replay de un webhook chocan contra el
  UNIQUE y devuelven el trabajo existente (mismo patrón que ventas/pedidos).
- **Reimprimir** = trabajo NUEVO con clave nueva (`…:r{n}`) ligado al original
  (`reimpresion_de FK`). Auditable: quién y cuándo.
- Generación automática: pedido confirmado → **un trabajo POR comanda/zona**; precuenta y
  comprobante bajo demanda desde el dashboard. El trabajo nace en la MISMA transacción que la
  comanda (si el pedido no confirma, no hay papel).

## D3 — Entrega al agente: SSE + ack explícito, idempotente de punta a punta

- API bajo `/api/v1/impresion`: `GET /cola` (trabajos `pendiente` del dispositivo, long-poll/SSE
  reusa el bus de eventos), `POST /{id}/ack` (impreso | error con detalle), `POST /{id}/reimprimir`.
- El estado `entregado_agente` NO es terminal: si el agente muere después de recibir y antes de
  imprimir, el trabajo expira de vuelta a `pendiente` (timeout) y el UNIQUE + el registro local
  del agente (últimos ids impresos) evitan el papel doble. **Corte de conexión a mitad de trabajo
  no duplica impresión** (condicional R2, testeado con impresora falsa).
- Evento SSE `impresion_trabajo` acotado al tenant (como todo el bus).

## D4 — Mapeo impresora↔zona

La zona de comandas ya existe (ADR 0032 D5). El mapeo `zona → impresora física (+ ancho)` es
**config local del agente** (archivo en la sede), no esquema del backend: el backend rutea por
`zona_id` y el agente decide a qué impresora física va cada zona. Evita CRUD de impresoras en el
dashboard para v1; si mañana hay multi-sede con gestión central, se promueve a tabla.
Perfil de impresora **"genérico ESC/POS" conservador por defecto** (las térmicas chinas difieren
en corte/acentos); perfiles específicos opt-in.

## D5 — Agente local (Python, Windows primero)

- `tools/agente_impresion/`: mismo stack (Python + `python-escpos`), pequeño y sin estado de
  negocio. Login con **token de dispositivo**, loop de cola, render ESC/POS desde el payload
  determinista, ack/error con reintento y backoff, log local rotado.
- Empaquetado **Windows primero** (PyInstaller, un .exe + config) — es el mercado local. Guía de
  instalación en runbook. Mac/Linux después (el código ya es portable).
- Render de plantillas **compartido backend/agente** (módulo puro): las 3 plantillas (§A.3 del
  goal: comanda, precuenta con propina Ley 1935, comprobante) se testean por golden test del
  buffer ESC/POS × ancho — el agente no formatea, ejecuta.

## D6 — Seguridad: token de dispositivo por tenant

Token opaco por dispositivo/sede, emitido desde el dashboard (admin), guardado **cifrado en el
control DB** (patrón secretos por empresa, `SECURITY.md`). El token resuelve tenant + dispositivo;
solo autoriza la superficie `/api/v1/impresion` (jamás endpoints de negocio). Revocable desde el
dashboard. El aislamiento multi-tenant es el de siempre: la cola vive en la DB del tenant y la
conexión ya apunta ahí.

## D7 — Flag `impresion`

Feature fina en `OPCIONALES` con dependencia **OR**: `pack_pedidos` o `pack_mesas` o `ventas`
(cualquier negocio con tickets puede imprimir; no es exclusiva de restaurantes). Sin flag: 404 en
toda la superficie (patrón `require_feature`). El manifiesto `restaurante-demo` la prende (R5).

## Consecuencias

- 1 migración tenant aditiva (`trabajos_impresion`), NULL-safe, up/down limpia.
- Un artefacto nuevo a operar (el .exe del agente): se documenta instalación y troubleshooting en
  el runbook; la validación con térmica física la hace Andrés (checklist Parte C del goal).
- El fallback navegador garantiza que un tenant sin agente NO queda bloqueado — degrada a
  imprimir desde la pestaña, como hoy hace media industria.
- Fases: R1 cola+API, R2 agente+fallback, R3 plantillas golden, R5 smoke E2E (goal §A.5).
