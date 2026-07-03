# Plan de impulso — Plataforma de agentes IA + diseño del dashboard (11 jun 2026)

> Plan estratégico pedido por Andrés: profundizar la capa de **agentes IA de atención al cliente**
> (más allá de lo fiscal/POS) y elevar el **frontend del dashboard de agentes** con propuestas de
> diseño white-label para mostrar a clientes. Acompaña (no reemplaza) a `roadmap.md`,
> `roadmap-superficies-web.md`, `whatsapp-agentes-arquitectura.md` y `pack-agenda-citas.md`.
> Las propuestas de diseño viven en `docs/design/propuestas/`.

---

## 1. Lectura del momento

Lo difícil ya existe: multi-tenant DB-per-tenant, runtime de agente con function-calling + bypass,
fiscal DIAN (FE + POS electrónico a punto de prenderse), worker ARQ, feature flags, provisionador por
manifiesto, onboarding mágico. El pack **agenda/citas** y el canal **WhatsApp** están diseñados
(ADR 0006, pack-agenda-citas, whatsapp-agentes-arquitectura).

El reposicionamiento correcto **no es** "software contable con bot": es
**"empleados digitales para negocios colombianos"** — agentes de WhatsApp que atienden, agendan,
venden, cobran y hacen seguimiento 24/7, con el POS/fiscal como pack para quien lo necesite.
Lo fiscal es la *cuña de entrada* ("te pongo legal con la DIAN"); los agentes son el *moat* y la
razón para quedarse y pagar cada mes.

**El bottleneck sigue siendo el primer cliente real.** Todo lo de abajo está ordenado para acortar
ese camino, no alargarlo.

---

## 2. Catálogo de agentes (packs) — existentes + nuevos

Cada agente es un **pack**: tablas en la DB del tenant + motor determinista + herramientas
function-calling + tab en el dashboard + flag. Mismo patrón que `pack_agenda` (el agente nunca
calcula; entiende intención y llama herramientas; todo acotado al teléfono que escribe).

| # | Pack | Vertical objetivo | Estado | Reusa |
|---|---|---|---|---|
| 1 | `pack_agenda` (citas) | clínicas, barberías, spas, vets | **Diseñado** — implementar primero | worker ARQ, Google Cal (ADR 0006) |
| 2 | `pack_faq` (conocimiento) | todos | Diseñado (RAG pgvector por tenant) | TabConocimiento ya existe |
| 3 | `pack_pedidos` (domicilios) | restaurantes, tiendas, ferreterías | **Nuevo — prioridad alta** | catálogo, inventario, ventas del POS |
| 4 | `pack_cobranza` (recordatorios) | todos los que fían/cobran cuotas | **Nuevo — quick win** | módulo fiados + worker + plantillas |
| 5 | `pack_ventas` (cotizaciones) | ferreterías, distribuidores, B2B | **Nuevo** | bypass + fuzzy match + catálogo (es el cerebro de FerreBot mirando hacia afuera) |
| 6 | `pack_postventa` (encuestas/reseñas) | todos | **Nuevo — barato** | worker + plantillas |
| 7 | `pack_reservas` (hotel) | hoteles/hostales de playa Cartagena | **Variante de agenda** | mismo motor: `recursos` tipo `habitación`, slots de noches |

### 3 — `pack_pedidos` (pedidos y domicilios por WhatsApp)

Cliente escribe "me mandas 2 hamburguesas y una coca-cola" → el agente arma el pedido contra el
catálogo del tenant, confirma dirección y método de pago, crea la orden y notifica al negocio.

- **Datos:** `menu/catalogo` (reusa `productos`), `zonas_domicilio` (barrio → tarifa), `pedidos`
  (estado: `recibido → confirmado → en_preparacion → en_camino → entregado | cancelado`),
  `pedido_config` (horarios de cocina, mínimo de pedido, tiempo estimado).
- **Herramientas:** `ver_menu()`, `armar_pedido(items)`, `confirmar_pedido(direccion, pago)`,
  `estado_mi_pedido()`, `escalar_humano()`.
- **Motor:** validación de stock/horario, cálculo de domicilio por zona, idempotencia por mensaje.
- **Dashboard:** tablero kanban de pedidos en vivo (SSE ya existe) — es LA pantalla del restaurante.
- **Cobro:** etiqueta de pago v1; QR Bre-B/link cuando exista el frente de pagos (ADR 0013 pendiente).
- **Por qué:** vertical enorme (87% de mipymes vende por WhatsApp); el pedido es la conversación
  WhatsApp más natural de Colombia; conecta directo con POS + POS electrónico (el pedido ES una venta
  → emite documento equivalente). Nadie pequeño en Cartagena tiene esto bien hecho.

### 4 — `pack_cobranza` (recordatorios de cartera, tono amable)

El negocio tiene cartera (fiados de la ferretería, cuotas de la clínica, mensualidades del gym).
El agente envía recordatorios programados por plantilla y **atiende la respuesta**: promesa de pago,
"ya pagué" (adjunta comprobante → bandeja humana), reclamo.

- **Datos:** reusa `fiados`/`cuentas_por_cobrar`; añade `cobranza_config` (cadencia, tono, tope de
  recordatorios, horario permitido) y `promesas_pago`.
- **Herramientas (cara al deudor):** `mi_saldo()`, `prometer_pago(fecha)`, `reportar_pago()`,
  `escalar_humano()`. Nunca ve deudas de otros; identidad = teléfono.
- **Guardarraíles:** tono respetuoso fijado por sistema (no configurable a agresivo), máximo N
  recordatorios, ventanas horarias, opt-out. Habeas Data: solo nombre+teléfono+saldo.
- **Por qué quick win:** Punto Rojo ya tiene fiados reales → se prueba con el tenant #1 sin vender
  nada nuevo; ROI medible en pesos recuperados (argumento de venta brutal: "el agente se paga solo").

### 5 — `pack_ventas` (cotizaciones y carrito por WhatsApp)

El cerebro que ya vende *hacia adentro* (vendedor por Telegram) apuntado *hacia afuera* (cliente
final por WhatsApp): "¿a cómo el bulto de cemento? ¿tienes drywall?" → cotiza con el catálogo real,
arma carrito, genera cotización PDF o reserva el pedido.

- **Reusa:** fuzzy match, aliases, precios escalonados/mayorista, bypass (≈60% sin IA → costo casi cero).
- **Datos nuevos:** `cotizaciones` (vigencia, estado), `ventas_config` (mostrar stock sí/no, precios
  públicos vs mayorista por cliente identificado).
- **Guardarraíl clave:** el agente **nunca inventa precio ni stock** — solo herramientas; si el
  producto no está, ofrece escalar.
- **Por qué:** es la extensión más natural del activo más maduro del proyecto; para ferreterías es
  la versión "hacia el cliente" del mismo producto que ya compraron.

### 6 — `pack_postventa` (seguimiento, reseñas, recompra)

Jobs programados tras un evento (cita cumplida, pedido entregado, venta): encuesta corta (1-5),
pedir reseña en Google Maps (link directo del tenant), recordatorio de recompra/control
("tu profilaxis fue hace 6 meses").

- **Datos:** `postventa_config` (qué disparadores, tiempos, plantillas), `encuestas_respuestas`.
- **Por qué barato:** es 90% worker + plantillas + 2 herramientas; sube retención del cliente final
  y da al dueño una métrica que le encanta (satisfacción, reseñas).

### 7 — `pack_reservas` (hoteles de playa)

**No es un pack nuevo: es el motor de agenda con otra cara.** `recursos` tipo `habitacion`,
slots = noches (check-in/check-out), `capacidad` = huéspedes, `requiere_anticipo=true` (ya
diseñado en `agenda_config`). El agente responde disponibilidad de fechas, tarifas por temporada,
y pide anticipo para confirmar (cobro real cuando exista Bre-B/link; mientras: `manual` +
"pendiente de pago"). Cartagena está llena de hoteles boutique/hostales que atienden WhatsApp a
mano — vertical local perfecto y de ticket alto.

---

## 3. Secuencia propuesta (no rompe el camino crítico vigente)

El roadmap de superficies (A1 login → A2 packs dashboard) y el switch-on POS siguen siendo el
camino crítico de M1. Esto se intercala así:

**Ola 0 — ya en curso (no tocar):** switch-on POS electrónico Punto Rojo; A1 login real; A2 packs
en dashboard (ADR 0008).

**Ola 1 — el primer agente vivo (4-6 semanas de prompts a Claude Code):**
1. Implementar `pack_agenda` (migración + motor + herramientas + tests) — ya está 100% especificado.
2. Adaptador WhatsApp v1 (Kapso ya elegido como puente; webhook + resolución de tenant + dedup).
3. `pack_cobranza` v1 probado con los fiados reales de Punto Rojo.
4. **Demo vendible:** clinica-demo con agenda por WhatsApp + dashboard limpio + diseño nuevo.

**Ola 2 — amplitud comercial:**
5. `pack_pedidos` (restaurantes) + tablero kanban en dashboard.
6. `pack_ventas` (cotizaciones ferretería hacia afuera).
7. `pack_reservas` (variante hotel) + propuesta de diseño hotel.
8. Frente pagos: ADR 0013 Bre-B/Wompi → habilita anticipos y cobro en pedidos.

**Ola 3 — retención y embudo:**
9. `pack_postventa`.
10. Landing + billing (Fase C del roadmap de superficies) + panel super-admin (B1).
11. Analítica del dueño: citas, conversión, no-shows, pesos recuperados por cobranza —
    la razón para entrar al dashboard a diario.

**Regla de oro:** cada pack nuevo entra por ADR → prompts por fase → review de diff real → CI verde,
igual que todo lo demás. Un pack a la vez hasta que el primero esté facturando.

---

## 4. Frontend — diseño del dashboard de agentes

### Diagnóstico

El dashboard actual (React + `DESIGN.md`) hereda el tema rojo POS de Punto Rojo. TabAgenda,
TabConversaciones y TabConocimiento existen pero sin identidad propia: una clínica ve la estética
de una ferretería. El white-label hoy es "un color primario", no un tema.

### Decisión de diseño: 3 propuestas navegables (en `docs/design/propuestas/`)

| Propuesta | Vertical demo | Lenguaje visual |
|---|---|---|
| **Aurora** (`propuesta-aurora-clinica.html`) | Clínica odontológica | Clínico-calmo: teal/menta, blanco, tarjetas suaves, tipografía humanista |
| **Brisa** (`propuesta-brisa-hotel.html`) | Hotel de playa Cartagena | Cálido-premium: arena/océano, serif display, fotos grandes, tarifas y ocupación |
| **Lienzo** (`propuesta-lienzo-generico.html`) | Multi-vertical configurable | Neutro-pro: sidebar oscura, acento configurable en vivo (selector de marca), denso en datos |

Las tres comparten **estructura** (mismo modelo de páginas, distinta piel) para demostrar el punto
white-label: *mismo producto, tu marca*. `index.html` las presenta lado a lado para enseñarlas a
clientes.

### Modelo de páginas del dashboard de agentes (lo que hay que diseñar)

1. **Hoy / Inicio** — el "buenos días" del dueño: citas de hoy, conversaciones esperando humano,
   métricas del día (atendidas por el agente vs escaladas).
2. **Agenda** — calendario semanal/diario por recurso, crear/mover citas, estados
   (pendiente/confirmada/cumplida/no-show).
3. **Conversaciones** — bandeja WhatsApp: lista + hilo, etiqueta "atendió el agente" vs "necesita
   humano", tomar control (handoff).
4. **Pedidos** (pack_pedidos) — kanban en vivo: recibido → preparación → en camino → entregado.
5. **Cartera** (pack_cobranza) — saldos, promesas de pago, pesos recuperados.
6. **Conocimiento** — lo que el agente sabe: servicios, FAQ, documentos; "entrenar" al agente.
7. **Agente (configuración)** — persona/tono, horarios, reglas, qué puede y no puede hacer,
   vista previa de conversación.
8. **Reportes** — citas, conversión, no-shows, satisfacción, recuperado.
9. **Ajustes del negocio** — branding (logo, color → alimenta el theming), horarios, equipo.

### Páginas fuera del dashboard (inventario completo, por orden)

- **Login / recuperar contraseña** — A1, ya planificado (ADR 0009); aplicar el tema elegido.
- **Panel super-admin** — B1: form → manifiesto → provisionador (la "piel" del backend ya hecho).
- **Landing pública** — C1: vende los agentes por vertical (clínica/hotel/restaurante/ferretería),
  con demo de conversación en vivo.
- **Billing/planes** — C2.
- **Página de estado/uptime** — confianza, barata.

### Sistema de theming (cuando se porte a React)

Tokens CSS por tenant servidos por `GET /api/v1/config` (ya existe): `--brand-primary`,
`--brand-surface`, `--brand-radius`, tipografía, logo, modo claro/oscuro. Las tres propuestas están
construidas sobre custom properties precisamente para que portarlas sea mecánico: la elegida se
convierte en el tema base y las variaciones son presets que el cliente elige en el onboarding
(el manifiesto ya tiene sección de branding).

---

## 5. Métricas de éxito

- **M-agente:** % de conversaciones resueltas sin humano; citas agendadas/semana por el agente;
  pesos recuperados por cobranza (Punto Rojo).
- **M-negocio:** primer cliente real pagando (sigue siendo EL hito); demo→cierre con las propuestas
  de diseño; tiempo de onboarding < 1 día con manifiesto.
- **M-producto:** un pack nuevo no toca el runtime (solo datos+herramientas+flag) — si lo toca,
  el diseño del pack está mal.

---

## 6. Riesgos y guardarraíles

- **Dispersión** (riesgo #1): 5 packs nuevos no pueden arrancar a la vez. La ola 1 es agenda +
  cobranza y NADA más hasta que haya un agente vivo con cliente real.
- **Costos de plantilla WhatsApp:** recordatorios/cobranza/postventa son mensajes pagos iniciados
  por el negocio → medirlos por tenant y reflejarlos en planes (cuota de plantillas/mes).
- **Meta ene-2026:** bots especializados permitidos; mantener cada agente acotado a su caso de
  negocio (ya cumplimos).
- **Habeas Data:** cobranza y postventa tocan datos sensibles del cliente final → mínimos, opt-out,
  retención definida antes del primer tenant externo.
- **Seguridad del agente:** toda herramienta acotada al teléfono que escribe; nunca operaciones
  destructivas; el agente jamás calcula precios/cupos/saldos (solo el motor).
