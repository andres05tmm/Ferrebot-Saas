# Plan — Demo restaurante Sirius (Cartagena)

> **Fecha de la demo: mañana.** Este plan está ordenado por prioridad de recorte: si el tiempo no
> alcanza, se corta desde abajo y la demo sigue siendo mostrable.
>
> Cowork = planning y prompts (este documento). Claude Code = ejecución (prompts en §8).
> Última actualización: 17 jul 2026.

## 0. Objetivo y guion de la demo

**El objetivo NO es enseñar features: es que Sirius SE VEA A SÍ MISMO operando.** Todo lo que
aparezca en pantalla debe ser SU negocio: su nombre, sus platos reales (los de las fotos de su
POS: carne asada, cerdo bistec, mojarra frita, sopa grande…), su menú que rota por día, sus
barrios de domicilio. El prospecto tiene que salir de la reunión pensando "así funcionaría MI
almuerzo de mañana", no "qué bonito software". Cada decisión de recorte (§6) se toma con esa
vara: se corta lo que no aporte a que el flujo pedido → pago → cocina se vea completo y real.

Sirius es un restaurante cartagenero de almuerzos / comida corriente. La demo cuenta esta historia,
de corrido, en ~5 minutos:

1. Un cliente le escribe al bot de Telegram **@SiriusBot** (para producción será WhatsApp; ver §7).
2. Pide el menú → el bot muestra la carta real de Sirius (la de su POS, por categorías, con la
   rotación del día explicada).
3. Arma el pedido conversando ("mándame una carne asada y una sopa grande") → el bot resuelve
   contra el catálogo, valida horario de cocina y da el total con domicilio por barrio (Manga,
   Getsemaní, Bocagrande…).
4. Confirma con dirección y método de pago **transferencia** → el bot da el número de cuenta/Nequi
   y el total exacto a transferir.
5. **El pedido aparece al instante en el kanban de cocina** (dashboard, pestaña Pedidos, SSE en vivo).
6. El cliente transfiere → llega el correo de Bancolombia → **el sistema detecta el pago solo**,
   marca el pedido como pagado (insignia en el kanban) y le confirma al cliente por Telegram:
   "¡Pago recibido! Tu pedido entró a cocina".
7. Cocina mueve la tarjeta (`en_preparacion → en_camino`) → el cliente pregunta "¿cómo va mi pedido?"
   y el bot le responde el estado real.
8. Momento asesor: el cliente pide "hablar con una persona" → `escalar_humano` → la conversación
   aparece en la pestaña Conversaciones del dashboard para que el staff la continúe.

## 1. Lo que YA existe (no construir dos veces)

La plataforma (ferrebot-saas / Melquiadez) ya tiene casi todo el demo construido y probado:

| Pieza | Dónde vive | Estado |
|---|---|---|
| Pack de pedidos completo (ADR 0016): motor determinista, menú = catálogo POS, borrador por cliente, horario de cocina, zonas de domicilio, mínimo, ciclo `recibido→confirmado→en_preparacion→en_camino→entregado` | `modules/pedidos/` + `ai/pedidos_tools.py` | ✅ en `main` |
| Kanban de cocina en vivo (SSE) | `dashboard/src/tabs/TabPedidos.jsx` + router `/api/v1/pedidos` | ✅ |
| Agente de cara al cliente (bucle LLM + herramientas por flag + memoria Redis por cliente + saneamiento ADR 0023) | `apps/wa/agent.py` (`correr_bucle`, `AgenteWa`, `MemoriaWa`) | ✅ (hoy solo transporte Kapso/WhatsApp) |
| Escalar a humano (núcleo, siempre disponible) | `ai/handoff_tools.py` + `modules/conversaciones/` + `TabConversaciones.jsx` | ✅ |
| Detección de transferencias Bancolombia por Gmail (la lógica de punto rojo, ya portada de bot-ventas-ferreteria) | `modules/bancos/gmail/` (parser + ingesta idempotente) + `apps/worker/bancolombia.py` | ✅ (notifica a Telegram + SSE `transferencia_recibida`) |
| Solicitudes de cobro con estado `pendiente→pagado` idempotentes por (origen=pedido, origen_id) | `modules/pagos/` (tabla `cobros`, ADR 0013) + `TabCobros.jsx` | ✅ (modo `manual` sin PSP) |
| Plantilla de tenant restaurante + branding cálido "brasa" + provisionador de un paso | `tools/onboarding/restaurante-demo.manifest.example.yaml` + `tools/provision_from_manifest.py` | ✅ |
| FAQ del negocio (`pack_faq`) | `modules/faq/` | ✅ |

**Las DOS piezas nuevas que hay que construir** (§3 y §4): el transporte Telegram del agente de
clientes, y el puente transferencia-detectada → pedido-pagado.

## 2. M0 — Tenant Sirius (sin código, solo datos)

1. Copiar `restaurante-demo.manifest.example.yaml` → `tools/onboarding/sirius.yaml`.
2. `identidad`: slug `sirius`, nombre "Sirius", rubro `restaurante`, NIT ficticio único.
3. `plan.nombre`: "Demo Sirius" (nombre propio: los planes se comparten por nombre).
   `features`: `["pos", "pack_pedidos", "pack_faq", "canal_whatsapp"]` — para la demo se reusa
   `canal_whatsapp` como flag del agente de clientes (gobierna el catálogo de herramientas del
   agente público sin importar el transporte); renombrarla a `canal_publico` queda para después.
4. **Menú:** extraído de las fotos que pasó el restaurante (skill onboarding-magico, contrato ADR
   0011). ⚠️ Los PRECIOS son inventados (el restaurante no los dio): van marcados con comentario
   `# PRECIO ESTIMADO — confirmar con Sirius` en el YAML y se corrigen en 5 minutos cuando los den.
   Precios de referencia usados: almuerzo corriente $15.000–18.000, platos a la carta cartageneros
   $22.000–38.000, jugos $6.000–8.000.
5. `packs.pedidos.config`: horario real de almuerzo (ej. 11:00–16:00), mínimo $15.000, domicilio
   default $4.000; `zonas` con barrios cartageneros reales (Centro, Getsemaní, Manga, Bocagrande,
   Crespo…).
6. `packs.faq`: horario, zonas de domicilio, formas de pago (efectivo / transferencia Bancolombia
   o Nequi), tiempo estimado.
7. `branding`: preset `brasa` (cálido, ya hecho) con `nombre_comercial: "Sirius"`. Home `/pedidos`.
8. Provisionar: `python -m tools.provision_from_manifest --from tools/onboarding/sirius.yaml`
   y validar con `pytest tests/test_manifests_demo.py` + `tools/verify_tenant.py`.

## 3. M1 — Canal Telegram del agente de clientes (la pieza grande)

**Qué es:** un bot de Telegram normal e independiente (@SiriusBot, token propio de BotFather),
cuyo "cerebro" es el runtime del agente de clientes que YA existe (`apps/wa/agent.py`). No se
reimplementa nada de lógica de pedidos/menú/FAQ/escalar: solo se cambia el tubo de entrada/salida.

**Por qué así y no un bot suelto:** el runtime ya sabe dar menú, armar y confirmar pedidos contra
el catálogo real, responder FAQ, escalar a humano, manejar memoria por cliente y sanear entradas.
Un bot suelto habría que dotarlo de todo eso en una noche. Y la migración a WhatsApp en producción
se vuelve trivial: mismo cerebro, se enchufa el número Kapso (`tools/seed_wa_numero.py`) y listo.

Diseño (espejo de `apps/wa/`, archivo nuevo `apps/tg_publico/`):

- **Entrada — webhook** `POST /tg-publico/{slug}`: valida el `secret_token` de Telegram
  (header `X-Telegram-Bot-Api-Secret-Token`, fail-closed como la firma de Kapso), dedup por
  `update_id` en Redis (mismo patrón `RedisWaDedup`), resuelve el tenant por slug (control DB,
  empresa activa), ignora todo lo que no sea mensaje de texto privado, responde 200 rápido y
  encola el turno en ARQ (job `atender_mensaje_tg`, espejo de `atender_mensaje_wa`).
- **Identidad del cliente:** `cliente_telefono = "tg:{chat_id}"`. Todo el dominio (pedidos,
  conversaciones, cobros, memoria) usa el teléfono como string opaco, así que funciona sin tocar
  nada. En WhatsApp la identidad pasa a ser el número real — cero cambio de lógica.
- **Contexto:** `Contexto(tenant_id, usuario_id=0, rol="cliente", origen="telegram",
  cliente_telefono="tg:{chat_id}")` — mismos guardarraíles del canal público (el teléfono jamás
  viene del modelo).
- **Salida — sender:** adaptador con la interfaz de `KapsoSender` que envía por la Bot API
  (`TelegramNotificador.responder` ya existe en `apps/bot/telegram.py`; reusar/extender).
- **Token del bot:** cifrado en secretos del tenant (clave nueva `tg_publico_bot_token`, mismo
  mecanismo que el bot interno) + registro del webhook con `setWebhook` (tool pequeño
  `tools/set_tg_publico.py` que guarda el token y registra el webhook con el secret).
- **Persona del agente:** `construir_system` ya parametriza por `rubro` y datos del tenant; con
  `rubro=restaurante` el prompt sale de restaurante. Revisar que el saludo diga "Sirius".
- **Reply del staff (escalado):** nota consciente — la respuesta del staff desde
  TabConversaciones hoy sale por Kapso. Adaptación mínima: si el teléfono empieza con `tg:`,
  responder por la Bot API. Si no alcanza el tiempo, para la demo basta MOSTRAR que la
  conversación escalada aparece en el dashboard (el reply en vivo es nice-to-have, §6).

**Exposición pública del webhook para la demo:** correr local (Postgres + Redis en Docker, ya
montados) y exponer solo el webhook con un túnel `cloudflared`/`ngrok` hacia `uvicorn` local.
Alternativa si Railway del piloto ya está arriba: registrar el webhook contra Railway. Decidir al
empezar según qué esté vivo; el túnel local es lo más controlable para mañana.

## 4. M2 — "Saber cuándo el cliente pagó" (adaptación de la lógica punto rojo)

La cadena completa, pieza por pieza (solo el paso 3 es código nuevo):

1. **Al confirmar el pedido con `metodo_pago=transferencia`**, crear la solicitud de cobro:
   `PagosService.crear_cobro(origen="pedido", origen_id=pedido.id, monto=total,
   cliente_telefono=...)` — ya es idempotente por (origen, origen_id), nace `pendiente` en modo
   manual. El agente responde con el total exacto y los datos de la cuenta (Nequi/Bancolombia del
   negocio, desde config del tenant). *(Cableado pequeño en `ai/pedidos_tools.py` /
   `modules/pedidos/service.py`.)*
2. **La ingesta Gmail-Bancolombia ya detecta la transferencia entrante** (`modules/bancos/gmail/`):
   parsea monto/remitente/hora, inserta idempotente en `bancolombia_transferencias`, notifica a
   Telegram del negocio y emite SSE. Es la lógica de bot-ventas-ferreteria ya portada.
3. **NUEVO — puente transferencia → cobro → pedido** (`modules/pagos/conciliador_transferencias.py`
   o similar): al insertar una transferencia entrante, buscar cobros `pendiente` con
   `origen="pedido"` y `monto` igual, de las últimas N horas. **Regla dura (igual que ADR 0028):
   solo con EXACTAMENTE UN candidato** se marca el cobro `pagado` (reusa `_repo.marcar`) y se
   emite SSE `pedido_pagado` + notificación al cliente por su canal ("¡Pago recibido! 🎉 Tu pedido
   entró a cocina") + aviso al grupo del negocio. Con 0 o ≥2 candidatos no se toca nada: queda
   para confirmar a mano en TabCobros (`marcar_pagado_manual`, que dispara la misma cascada).
   Enganche: el mismo camino de `procesar_push` del worker (callback `publicar`/nuevo hook).
4. **Kanban:** insignia "Pagado ✓" en la tarjeta del pedido (join pedido→cobro en el listado del
   router de pedidos + refresco por el SSE `pedido_pagado` en `TabPedidos.jsx`).

**Plan B para el escenario (imprescindible tenerlo):** la demo NO puede depender de que el correo
real de Bancolombia llegue en vivo (el buzón del watch es de punto rojo, no de Sirius; y Gmail
puede tardar). Script `tools/demo_transferencia.py <slug> <monto>` que inyecta una transferencia
entrante por el MISMO camino de la ingesta (mismo parser de fixture, misma idempotencia, mismo
puente). En la demo: el cliente "transfiere", Andrés corre el comando (o un botón super-admin) y
toda la cascada real se dispara — pago detectado, kanban, notificación. Es el mismo código de
producción; lo único simulado es el correo. Si el buzón real de pruebas está configurable a
tiempo, mejor aún: se muestra con una transferencia real de $100.

## 5. M3 — Dashboard de cocina listo para mostrar

Ya existe casi todo (TabPedidos kanban + SSE + preset brasa + home `/pedidos` del vertical):

- Insignia "Pagado ✓" (viene de M2.4).
- Sembrar 3–4 pedidos del día en estados variados (`seed_demo_transaccional` ya lo hace para
  restaurante-demo; apuntarlo a sirius o sembrar a mano) para que el kanban no arranque vacío.
- Verificar theming: nombre "Sirius" en el shell, colores brasa, tabs correctas (sin tabs de
  ferretería que confundan al prospecto — el gating por features ya lo resuelve).
- Login demo listo (`demo+sirius@…`, rol vendedor) y sesión abierta ANTES de la reunión.

## 6. Orden de ejecución y líneas de corte (para esta noche)

| # | Entregable | Sin esto NO hay demo | Estimado |
|---|---|---|---|
| 1 | M0 manifiesto sirius + provisión + seed | Sí | corto |
| 2 | M1 canal Telegram (webhook + job + sender + set_tg_publico) | Sí | el grueso |
| 3 | M2.1 cobro al confirmar + respuesta con datos de pago | Sí | corto |
| 4 | M2 plan B `tools/demo_transferencia.py` + puente monto→cobro→pagado + SSE | Sí (es EL efecto wow) | medio |
| 5 | M2.4 insignia Pagado en kanban | No (TabCobros lo muestra igual) | corto |
| 6 | M3 pulido (seed, theming, login listo) | No, pero barato | corto |
| 7 | Reply del staff por Telegram en conversación escalada | No (mostrar solo la bandeja) | recorte primero |
| 8 | Gmail watch real para Sirius | No (plan B cubre) | posponer |

**Ensayo obligatorio al final:** correr el guion de §0 completo dos veces, con el comando del plan
B a la mano y el dashboard ya abierto en la pestaña Pedidos.

## 7. Migración a WhatsApp (respuesta lista para el prospecto)

"El bot que ven en Telegram es el mismo que atiende WhatsApp": el cerebro, las herramientas, la
memoria y el dashboard son idénticos; Telegram y WhatsApp son solo el tubo. Pasar a producción =
línea WhatsApp del negocio conectada por Kapso (`wa_numeros` + `tools/seed_wa_numero.py`), que ya
está construido y operando en las demos Melquiadez. Nada del trabajo de esta noche se bota: el
transporte Telegram queda además como canal de demos permanente (nota
`research/nota-doble-canal-telegram-whatsapp.md`: el canal público en producción es WhatsApp).

## 8. Ejecución en Claude Code: orquestación con subagentes en paralelo

> Dada la urgencia, se ejecuta con UN prompt maestro (§8.0) que orquesta subagentes en paralelo.
> Los briefs E1–E4 de abajo son las especificaciones de cada frente; el orquestador se las pasa a
> sus subagentes. Guardarraíles que ningún frente puede saltarse: secret del webhook fail-closed,
> identidad del cliente solo del payload (jamás del modelo), regla del candidato único en el
> puente de pagos, y suite verde antes de integrar.

### 8.0 Prompt maestro (con esto se inicia)

```
Lee docs/plan-demo-sirius.md COMPLETO antes de tocar nada. Mañana hay demo con el restaurante
Sirius; el objetivo de §0 manda: que Sirius se vea a sí mismo operando el flujo pedido → pago →
cocina con SUS platos. Ejecuta el plan orquestando subagentes EN PARALELO según §8:

1. Fija primero el contrato del evento pedido_pagado (§8.1, payload {pedido_id, cobro_id, monto}).
2. Lanza EN PARALELO cuatro subagentes, uno por frente, cada uno con su brief de §8.2 y la
   tabla de propiedad de archivos de §8.1 como límite duro: A (canal Telegram público),
   B (cobro + puente transferencia→pedido pagado + tools/demo_transferencia.py),
   C (validar y provisionar tenant sirius + seed), D (insignia Pagado en TabPedidos).
   Ningún subagente toca apps/api/main.py ni apps/worker/main.py: ese cableado es tuyo en
   integración. Si un frente descubre que necesita un archivo de otro, para y te lo reporta.
3. Cuando terminen: integra tú en serie (§8.3), corre la suite completa (pytest + Vitest) y
   NO integres nada en rojo.
4. Cierra con el ensayo de §8.3: guion de §0 de punta a punta dos veces contra el stack local
   (Postgres/Redis en Docker), pagando con tools/demo_transferencia.py. Deja escrito al final
   de docs/plan-demo-sirius.md el runbook de mañana: comandos exactos del túnel, del setWebhook,
   del plan B de pago, URL del dashboard y login demo.
Reglas de siempre: guardarraíles de seguridad de §8 (no negociables), SQL solo en repos,
secretos cifrados, hora Colombia, get_logger. Commits tipo: descripción, uno por frente
integrado. Si el tiempo aprieta, recorta según la tabla de §6 (de abajo hacia arriba) y dilo.
```

### 8.1 Mapa de paralelización (por qué estos frentes no chocan)

| Frente | Archivos que toca | Choca con |
|---|---|---|
| A = E1 canal Telegram | `apps/tg_publico/` (nuevo), `tools/set_tg_publico.py` (nuevo) | montaje en `apps/api/main.py` y registro del job en `apps/worker/main.py` → **diferir a integración** |
| B = E2 pagos | `modules/pagos/` (archivo nuevo), `ai/pedidos_tools.py`, `modules/pedidos/service.py`, `tools/demo_transferencia.py` (nuevo) | hook en `modules/bancos/gmail/ingesta.py` / `apps/worker/bancolombia.py` → puntual, avisar al orquestador |
| C = E3 tenant Sirius | `tools/onboarding/sirius.yaml` (ya está), datos vía provisionador | nada de código; puede correr de primero |
| D = E4 insignia kanban | `dashboard/src/tabs/TabPedidos.jsx`, `modules/pedidos/router.py` (join cobro) | consume el SSE `pedido_pagado` que define B → acordar SOLO el nombre/shape del evento al inicio |

A, B y D avanzan en paralelo sin pisarse si respetan esa tabla; los dos puntos compartidos
(`apps/api/main.py`, `apps/worker/main.py`) los cablea el orquestador en la fase de integración.
El contrato del evento (`pedido_pagado`, payload `{pedido_id, cobro_id, monto}`) se fija ANTES de
arrancar para que B y D no se esperen.

### 8.2 Briefs por frente (los reparte el orquestador)

**Brief A (=E1) — Canal Telegram público:**

```
Lee docs/plan-demo-sirius.md §3. Crea el canal Telegram del agente de clientes como espejo de
apps/wa/: paquete apps/tg_publico/ con webhook POST /tg-publico/{slug} (valida
X-Telegram-Bot-Api-Secret-Token fail-closed; dedup por update_id en Redis patrón RedisWaDedup;
resuelve tenant por slug en control DB; solo mensajes de texto privados; 200 rápido y encola),
job ARQ atender_mensaje_tg en apps/worker/main.py que reusa el bucle de apps/wa/agent.py con un
sender Bot API (reusa TelegramNotificador), identidad cliente_telefono="tg:{chat_id}", origen
"telegram". Respeta el handoff igual que atender_mensaje_wa: si esta_en_humano(telefono), NO
corras el agente (solo persiste el mensaje entrante del cliente en la conversación). Secreto del tenant: clave tg_publico_bot_token (mismo cifrado del bot interno) +
tools/set_tg_publico.py que guarda el token y hace setWebhook con secret_token. Tests: humo del
webhook (firma inválida 403, dedup, tenant no mapeado 200 sin abrir tenant) espejando
tests de apps/wa. No toques la lógica de pedidos ni el agente: solo transporte. NO toques
apps/api/main.py ni apps/worker/main.py: deja el router y el job exportados y documenta en tu
reporte final las 2 líneas de cableado que faltan (las hace el orquestador en integración).
```

**Brief B (=E2) — Cobro al confirmar + puente transferencia→pedido pagado:**

```
Lee docs/plan-demo-sirius.md §4. (a) Al confirmar_pedido con metodo_pago transferencia, crea el
cobro con PagosService.crear_cobro(origen="pedido", origen_id, monto=total, cliente_telefono) y
haz que el agente responda el total exacto + datos de pago del negocio (config del tenant, claves
pago_transferencia_titular/numero). (b) Nuevo conciliador: cuando la ingesta Bancolombia inserta
una transferencia entrante (modules/bancos/gmail/ingesta.py, hook junto a `publicar`), busca
cobros pendiente origen=pedido con monto igual en ventana de 6h; SOLO con exactamente un candidato
marca pagado, emite SSE pedido_pagado, notifica al cliente por su canal y al grupo del negocio;
con 0 o ≥2 no toca nada (queda en TabCobros para marcar_pagado_manual, que debe disparar la misma
cascada). (c) tools/demo_transferencia.py <slug> <monto>: inyecta una transferencia entrante por
el mismo camino de la ingesta (fixture de correo real de tests del parser), idempotente. Tests:
candidato único marca pagado; ambiguo no toca; replay no re-notifica. El SSE es exactamente
`pedido_pagado` con payload {pedido_id, cobro_id, monto} (contrato acordado con el frente D).
```

**Brief C (=E3) — Tenant Sirius:**

```
tools/onboarding/sirius.yaml ya está en el repo (menú real de las fotos del POS, precios
estimados). Valida con --check y corrige errores de esquema SIN cambiar datos del menú
(los nombres/precios son del insumo; si el validador exige algo del menú, repórtalo). Corre
provision_from_manifest, valida con tests/test_manifests_demo.py y tools/verify_tenant.py,
y siembra 3-4 pedidos del día en estados variados para que el kanban no arranque vacío.
El token de @SiriusBot y las claves pago_transferencia_* se configuran en integración
(necesitan el túnel y el tool del frente A).
```

**Brief D (=E4) — Insignia Pagado en el kanban:**

```
TabPedidos.jsx: insignia "Pagado ✓" en la tarjeta del pedido. El listado /api/v1/pedidos debe
unir el estado del cobro por (origen=pedido, origen_id); agrégalo si falta. Refresco en vivo
suscribiéndose al SSE pedido_pagado (payload {pedido_id, cobro_id, monto} — contrato con el
frente B; no esperes su código, trabaja contra el contrato). Test Vitest del render de la
insignia y del handler del evento.
```

### 8.3 Integración y ensayo (el orquestador, al final, en serie)

1. Cablear los 2 puntos compartidos: montar el router de A en `apps/api/main.py` y registrar
   `atender_mensaje_tg` en `apps/worker/main.py`; conectar el hook de B en el camino de la ingesta.
2. Suite completa verde (`pytest` + Vitest del dashboard). Ningún frente se integra en rojo.
3. Configurar lo operativo: token de @SiriusBot (`tools/set_tg_publico.py` con el túnel activo),
   claves `pago_transferencia_*` del tenant sirius.
4. **Ensayo del guion de §0 de punta a punta dos veces** (con `tools/demo_transferencia.py` como
   pago). Documentar en este archivo los pasos manuales que queden para la reunión (comando del
   túnel, comando del plan B, URL del dashboard, login demo).

## 9. Riesgos puntuales de mañana

- **Túnel caído en plena demo** → tener el comando del túnel y el `setWebhook` en un script de un
  paso; y grabar un video de respaldo del flujo completo en el ensayo de esta noche.
- **Latencia del LLM en vivo** → `gobierno`/`resiliencia` ya tienen fallback; ensayar en la misma
  red que se usará (¿celular como hotspot?).
- **El prospecto pregunta precios de la plataforma** → fuera de alcance de este plan; llevar la
  respuesta comercial aparte.
- **Datos reales** → todo Sirius es tenant demo aislado (DB propia); nada toca a Punto Rojo.

---

## 10. RUNBOOK de la demo (18-jul-2026) — escrito tras el ensayo del 17-jul

**Estado del ensayo (17-jul, noche):** guion de §0 corrido DOS veces de punta a punta contra el
stack local, en verde: menú real → pedido conversado → confirmación con total exacto + datos de
pago → cobro `pendiente` → `tools/demo_transferencia.py` → cobro `pagado` + insignia "Pagado ✓"
en el kanban (SSE en vivo) → "¿cómo va mi pedido?" con estado real → escalar a humano visible en
Conversaciones. Suites completas verdes (pytest exit 0 + Vitest 494/494). Pedidos del ensayo:
#9 ($30.000, tg:9001) y #10 ($41.000, tg:9002).

### 10.1 Único paso humano previo (Andrés, ANTES de la reunión)

1. ~~Crear el bot en BotFather~~ **HECHO (17-jul): @Siriussdemo_bot**, token guardado cifrado en
   el control DB (`tg_publico_bot_token`) y webhook registrado. Si el túnel cambia de URL,
   re-correr `set_tg_publico` con el MISMO token (BotFather → /mybots → API Token si se pierde).

### 10.2 Arranque del stack (4 terminales, ~3 min)

```bash
# 0. Docker (tras reiniciar el PC; Redis levanta solo)
docker start ferrebot-pg ferrebot-redis
pg_isready -h localhost -p 5433          # esperar "accepting connections"

# 1. API
.venv/Scripts/python.exe -m uvicorn apps.api.main:app --port 8000

# 2. Worker (el agente corre aquí)
.venv/Scripts/python.exe -m arq apps.worker.main.WorkerSettings

# 3. Dashboard (dashboard/.env.local ya dice VITE_TENANT_SLUG=sirius)
cd dashboard && npm run dev
```

### 10.3 Túnel + webhook del bot (cada vez que arranque el túnel)

```bash
# Túnel (ngrok 3.x ya instalado). Copiar la URL https://xxxx.ngrok-free.app
ngrok http 8000

# Registrar token + webhook (idempotente; re-correr si el túnel cambia de URL)
.venv/Scripts/python.exe -m tools.set_tg_publico sirius <TOKEN_BOTFATHER> https://xxxx.ngrok-free.app
```

Probar desde el celular: escribirle a @SiriusBot "menú" → debe responder la carta de Sirius.

### 10.4 Dashboard y login demo

- URL: **http://localhost:5173/pedidos** (kanban de cocina; dejarlo abierto ANTES de la reunión).
- Login demo: **demo+sirius@melquiadez.com / SiriusDemo2026** (rol vendedor).
- La pestaña Conversaciones muestra el escalado a humano; Cobros muestra la solicitud de cobro.

### 10.5 Plan B de pago (EL momento wow)

Cuando el cliente "transfiera", correr con el TOTAL EXACTO que dijo el bot (incluye domicilio):

```bash
.venv/Scripts/python.exe -m tools.demo_transferencia sirius 30000
# Si se repite el MISMO monto en el día (segundo pedido igual): agregar --ref 2
.venv/Scripts/python.exe -m tools.demo_transferencia sirius 30000 --ref 2
```

Efecto (todo código de producción; solo el correo es simulado): transferencia ingresada
idempotente → cobro del pedido `pagado` (regla de candidato único) → insignia "Pagado ✓" al
instante en el kanban (SSE) → mensaje al cliente por Telegram "¡Pago recibido! 🎉 Tu pedido #N
entró a cocina." Con 0 o ≥2 candidatos NO toca nada: cerrar a mano en Cobros (misma cascada).

### 10.6 Notas operativas / troubleshooting

- **Horario de cocina ampliado** a 07:00–21:00 en `pedido_config` de sirius (el real 11:00–15:30
  quedó en la FAQ) para que la demo nunca choque con "cocina cerrada". Restaurar después si molesta.
- **Menú vacío** = productos sin stock (el menú filtra `stock>0`). Ya se sembró stock 50 con
  movimientos de inventario idempotentes (`seed-sirius-stock-<id>`); si se re-provisiona desde
  cero, repetir ese seed.
- **Túnel caído en vivo** → relanzar `ngrok http 8000` + re-correr `set_tg_publico` con la URL
  nueva (dos comandos, <30 s).
- **Reply del staff desde Conversaciones** sale por Kapso (WhatsApp), no por Telegram — recorte
  consciente (§6 fila 7): en la demo se MUESTRA la bandeja con la conversación escalada, no se
  responde en vivo. "Marcar pagado" manual desde el dashboard actualiza kanban/SSE pero no
  notifica al cliente (el camino del worker y el plan B sí lo hacen).
- **`tools/onboarding/sirius.yaml` NO está en git** (los manifiestos reales van gitignored por
  diseño; solo los `.example` se versionan). Vive solo en esta máquina: sacarle copia si se
  quiere conservar. Los PRECIOS siguen siendo estimados (§2.4): corregirlos cuando Sirius los dé.
- **Aislamiento:** todo corre en la DB `ferrebot_sirius` local (empresa_id=8); nada toca prod ni
  a Punto Rojo.

### 10.0 ⭐ ACTUALIZACIÓN FINAL (17-jul ~10 pm) — LA DEMO CORRE EN PRODUCCIÓN

El PR #113 se mergeó a main (squash `9ffda11`, CI verde) y el tenant **`siriuss`** quedó
provisionado EN PRODUCCIÓN (empresa_id=8 en prod, DB propia). **Ya no se necesita túnel, ngrok,
ni stack local**: bot y dashboard corren sobre Railway.

- **Dashboard:** https://siriuss.melquiadez.com/pedidos — login **admin@siriuss.com /
  siriussdemo** (verificado: login 200 + kanban con platos del volante y barrios).
- **Bot:** @Siriussdemo_bot con webhook en `https://siriuss.melquiadez.com/tg-publico/siriuss`
  (verificado con getWebhookInfo: sin errores). La foto del volante se sirve de
  `https://siriuss.melquiadez.com/siriuss-menu.jpg` (config `menu_foto_path`).
- **Plan B de pago (en la reunión):**
  ```bash
  railway ssh "python -m tools.demo_transferencia siriuss <TOTAL-EXACTO>"
  # mismo monto dos veces en el día: agregar --ref 2
  ```
- **Re-preparar la demo antes de la reunión** (kanban fresco, idempotente):
  ```bash
  railway ssh "python -m tools.preparar_demo_sirius siriuss"
  ```
- Config ya seteada en prod: `pago_transferencia_titular/numero`, `menu_foto_path`, horario
  ampliado 07:00–21:00, stock 50, clave del admin.
- **El stack local (tenant `sirius`, §10.2–10.5) queda como PLAN C**: si prod fallara en vivo,
  re-registrar el webhook al túnel (`ngrok http 8000` + `set_tg_publico sirius <token> <url>`)
  y abrir localhost:5173.
- Pendiente de verificación humana: un mensaje real a @Siriussdemo_bot (el webhook y la ruta
  están verdes; falta el round-trip completo desde un celular).

### 10.0-bis PAGO REAL + COMPROBANTE DEL CLIENTE (PR #115, en prod)

- **Detección de pago REAL activa**: el buzón `ferreteriapuntorojo17@gmail.com` (el mismo de las
  alertas Bancolombia de Punto Rojo) quedó registrado para `siriuss` en modo **POLL** (cada
  minuto, sin tocar el watch Pub/Sub del sistema legado, que sigue intacto). Transferencia real a
  la cuenta de Punto Rojo → correo de Bancolombia → poll ≤1 min → conciliador (monto exacto, 6h,
  candidato único) → pedido PAGADO + kanban + aviso Telegram al cliente. Verificado en logs:
  `gmail_poll_baseline empresa_id=8`, cero errores.
- **⚠️ Para el pago real en la reunión**: poner la CUENTA REAL (la de Punto Rojo, adonde llega el
  correo) en lo que el bot dicta: `railway ssh "python -m tools.set_config siriuss
  pago_transferencia_numero '<cuenta real Bancolombia>'"` (hoy hay un placeholder). El monto
  transferido debe ser EXACTO al total del pedido.
- **Comprobante del cliente**: si el cliente manda la FOTO del comprobante (cualquier banco/
  billetera colombiana), el bot la lee con visión, la asocia a su cobro pendiente y responde el
  estado. La foto JAMÁS marca pagado (falsificable): asocia y sirve de DESEMPATE cuando dos
  pedidos comparten monto (paga solo el candidato con comprobante si es exactamente uno). Tabla
  `comprobantes_pago` (migración 0057, aplicada a todos los tenants por el preDeploy).
- El plan B (`railway ssh "python -m tools.demo_transferencia siriuss <monto>"`) sigue siendo el
  respaldo si el correo real tarda en plena reunión.

### 10.7 Actualización (17-jul noche) — volante REAL del restaurante

El restaurante pasó su volante oficial del día ("BUEN DÍA! — **SIRIUSS** COMIDA EJECUTIVA"):

- **Nombre real: SIRIUSS** (doble S) — actualizado en identidad, branding y el prompt del bot.
- **Catálogo = volante, precios REALES** (ya no estimados): sopa de hueso $14.000; 10 platos
  fuertes a $19.000 (carne asada, cerdo asado, carne/cerdo en bistec, pollo frito, albóndigas,
  lengua en salsa, sobrebarriga criolla, pechuga asada, salpicón de jurel); Menú especial $21.000.
  Los acompañantes (arroz blanco o de coco, tajadas, lentejas, ensalada de payaso) van INCLUIDOS
  → FAQ. Bocagrande +$1.000 por plato → FAQ (el recargo por-plato no existe en el pack; mejora
  futura). Los platos del POS del 16-jul que no están en el volante quedaron INACTIVOS (rotación).
- **Branding**: `color_primario #F08A21` (naranja del volante) sobre el preset brasa.
- **FOTO del menú por Telegram**: si el cliente pide el menú (regex menú/carta/almuerzo), el bot
  manda la IMAGEN del volante antes de la respuesta del agente. La foto vive en
  `tools/onboarding/sirius-menu.jpg` (gitignored, como el manifiesto) y se configura con:
  `python -m tools.set_config sirius menu_foto_path "<ruta absoluta al jpg>"` (ya seteado en esta
  máquina). Volante nuevo cada día = reemplazar el jpg (misma ruta, cero config). Best-effort: sin
  foto/token o con red caída, el turno sigue con el menú de texto.
- Kanban resembrado con platos del volante y barrios; el ensayo previo (#9/#10) quedó archivado
  por la resiembra. **Totales de referencia para el plan B**: plato fuerte $19.000 + domicilio
  Manga $4.000 = **$23.000**; especial $21.000 + Getsemaní $3.000 = **$24.000**.
