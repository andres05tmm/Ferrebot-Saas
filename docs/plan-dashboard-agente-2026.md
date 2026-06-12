# Plan — Dashboard del agente IA: IA limpia, inbox con hand-off y catálogo de temas

> Objetivo: dejar el dashboard listo como **producto de agente IA vendible** (no un POS de ferretería),
> de modo que conseguir un cliente sea solo **adaptar el tema a su empresa**. Tres fases en orden.
>
> Estado de partida (auditado jun-2026): el motor multi-tenant, el white-label por `branding.tema`
> (ADR tema Aurora) y el **backend del hand-off** ya existen. Las brechas son de UI y de persistencia
> del hilo de mensajes. Este doc es la guía para ejecutar con Claude Code, fase por fase.

## Principios (no romper)

- **Un solo código white-label.** Nada hardcodeado por slug; todo sale de features + branding de `GET /config`.
- **Multi-tenant intacto** (`.claude/rules/multitenancy.md`): la base ES la frontera; resolver tenant antes de consultar.
- **Reglas del repo:** `async/await` en endpoints con eventos, zona horaria Colombia, sin secretos, sin `print`, acceso a datos solo por repositorios, idempotencia donde aplique.
- **TDD + verificación:** tests del dashboard `cd dashboard && npm test -- --run`; backend `pytest -q` (incluye migraciones up/down); `engineering:code-review` antes de cerrar; capturas en light+dark de cada tenant tocado.

---

## Hallazgos que fundamentan el plan (no re-descubrir)

**Gating de navegación** — `dashboard/src/lib/features.jsx`:
- `RUTA_FEATURE` mapea ruta→capacidad. Tras ADR 0008 el POS ya NO es núcleo: `/ventas /caja /inventario /compras /proveedores /gastos /top-productos /kardex /historial` se gatean por `pos`.
- **Núcleo siempre visible:** `/hoy`, `/clientes`, `/resultados`. Aquí está el problema: `/hoy` es la home POS y se muestra siempre, incluso a una clínica.
- Packs de servicio: `/agenda`→`pack_agenda`, `/conversaciones`→`canal_whatsapp`, `/conocimiento`→`pack_faq`, `/cartera`→`pack_cobranza`, `/pedidos`→`pack_pedidos`.

**Hand-off (backend ya hecho)** — `modules/conversaciones/{router,service,repository}.py`, migración tenant `0009_conversaciones`:
- Tabla `conversaciones`: una fila por `cliente_telefono`, `estado` enum `('bot','humano')`, `motivo`, `creada_en/escalada_en/resuelta_en`. **No guarda el hilo de mensajes.**
- `apps/wa/agent.py`: si `esta_en_humano(telefono)` → **el agente NO corre** (pausa). Durante la pausa los mensajes entrantes se preservan (hoy en memoria/Redis, no en DB). Al resolver se limpia el historial y el bot retoma.
- Endpoints: `GET /conversaciones/escaladas`, `POST /conversaciones/{id}/resolver`. Eventos SSE: `conversacion_escalada`, `conversacion_resuelta`.
- `apps/wa/kapso.py`: `enviar_texto(phone_number_id, to, texto)` ya manda **texto libre** (válido dentro de la ventana de 24h — el cliente acaba de escribir).
- `dashboard/src/tabs/TabConversaciones.jsx`: solo LISTA escaladas y marca resuelto; comenta explícito que responder desde el dashboard es "futuro, fuera de alcance".

**Métricas** — `modules/reportes_agente/router.py`: `GET /api/v1/agente/reporte` ya entrega bloques por pack (citas, % sin humano, pedidos, recuperado, satisfacción).

**Temas** — `dashboard/src/index.css` (`[data-tema="aurora"]` light+dark), `dashboard/src/lib/config.js applyTheming` (setea `data-tema` desde `branding.tema`), 5 mockups en `design-propuestas/` (aurora clínica, brasa restaurante, brisa hotel, lienzo genérico, navaja barbería).

---

## FASE 1 — Reestructurar la IA del agente (home de agente, sin POS)

**Meta:** que un negocio de servicios (agenda/canal) vea un dashboard de agente al entrar, no la home POS. Bajo riesgo, alto impacto visual.

### Alcance
1. **Home por vertical.** La ruta home deja de ser fija a "Hoy" POS:
   - Si la empresa tiene `pos` → home = `TabHoy` actual (ferretería, sin cambios).
   - Si tiene packs de servicio (`pack_agenda` o `canal_whatsapp`) y NO `pos` → home = **`TabInicioAgente`** (nuevo).
   - Implementar como resolución de la ruta índice en `App.jsx` según features (mismo patrón de gating que `features.jsx`), sin hardcodear slug.
2. **`TabInicioAgente` (nuevo)** — composición de lo que ya existe:
   - **Citas de hoy** (reusa datos de `TabAgenda`/agenda service): próximas N citas con hora, servicio, cliente, estado.
   - **Conversaciones pendientes** (cuenta de `GET /conversaciones/escaladas`): "X clientes esperando asesor" con enlace al Inbox (Fase 2).
   - **KPIs del agente** (de `GET /agente/reporte`): % resuelto sin humano, citas de la semana, satisfacción, recuperado — máx. 5-7 tarjetas, según packs activos.
   - **Acciones rápidas** contextuales (Ver agenda, Abrir inbox, Conocimiento), no "Nueva venta".
3. **Gating de `/hoy`.** Mover `/hoy` a `RUTA_FEATURE['/hoy'] = 'pos'` y garantizar que el núcleo de servicio siga llegando a su home nueva. Ajustar `features.jsx`, `Sidebar.jsx` y el routing.
4. **Nav limpio** para servicios: Inicio, Agenda, Inbox, Conocimiento, Clientes, Reportes (los POS solo con `pos`).

### Archivos
`dashboard/src/lib/features.jsx`, `dashboard/src/App.jsx` (ruta índice), `dashboard/src/components/Sidebar.jsx`, `dashboard/src/tabs/TabInicioAgente.jsx` (+ test), reusar `TabAgenda`/agenda y `reportes_agente`.

### Criterios de aceptación
- [ ] clinica-demo (sin `pos`) entra y ve la home de agente con citas+pendientes+KPIs; NO ve Hoy/POS.
- [ ] Punto Rojo (con `pos`) sigue viendo Hoy POS idéntico a hoy.
- [ ] Nav gateado correcto en ambos; `npm test` verde (incluye test de la resolución de home por features).

### Prompt para Claude Code (Fase 1)
```
Lee docs/plan-dashboard-agente-2026.md (Fase 1) y dashboard/src/lib/features.jsx, App.jsx,
components/Sidebar.jsx, tabs/TabHoy.jsx, tabs/TabAgenda.jsx, y modules/reportes_agente/router.py.

Implementa la "home de agente": la ruta índice del dashboard debe resolver, según las features de
GET /config (sin hardcodear slug), a TabHoy (si la empresa tiene `pos`) o a un nuevo TabInicioAgente
(si tiene pack_agenda o canal_whatsapp y no pos). TabInicioAgente compone: próximas citas de hoy
(datos de agenda), conteo de conversaciones pendientes (GET /conversaciones/escaladas) con enlace al
inbox, y 5-7 KPIs de GET /agente/reporte según packs activos, más acciones rápidas de servicio.
Mueve /hoy a RUTA_FEATURE='pos' y asegura que el núcleo de servicio llegue a la home nueva. No cambies
nada del POS de Punto Rojo. TDD: tests de la resolución de home por features y del render de
TabInicioAgente. Corre `cd dashboard && npm test -- --run`. Pasa engineering:code-review.
Toma capturas de clinica-demo y Punto Rojo (light+dark).
```

---

## FASE 2 — Inbox con hand-off (lo que más vende)

**Meta:** el asesor responde al cliente **desde el dashboard** con el bot en pausa; al terminar, devuelve la conversación al bot. Modelo Chatwoot/Intercom (`pending`→`open`→`pending`) sobre tu `estado` actual (`bot`→`humano`→`bot`).

### Brecha central
`conversaciones` NO persiste el hilo. Para renderizar y responder hace falta **persistir los mensajes** (entrada y salida) en una tabla nueva del árbol TENANT.

### Alcance

**Backend**
1. **Migración tenant `00NN_conversacion_mensajes`** (up/down limpios): tabla `conversacion_mensajes`
   - `id` BigInteger PK
   - `cliente_telefono` Text (FK lógica a `conversaciones.cliente_telefono`) + índice
   - `direccion` enum `('entrante','saliente')`
   - `autor` enum `('cliente','bot','asesor')`
   - `texto` Text
   - `creada_en` TIMESTAMP tz (default now())
2. **Persistir mensajes** en el runtime WhatsApp (`apps/wa/`): cada entrante del cliente, cada respuesta del bot y cada respuesta del asesor → fila en `conversacion_mensajes` (no solo Redis). Mantener Redis para el contexto del LLM; la DB es la fuente del hilo visible.
3. **Endpoints nuevos** en `modules/conversaciones/router.py` (con repositorio, sin SQL suelto, `async`):
   - `GET /conversaciones/{id}/mensajes` → hilo ordenado.
   - `POST /conversaciones/{id}/responder {texto}` → valida estado=`humano`, manda por `kapso.enviar_texto` (número del tenant), persiste saliente `autor=asesor`, emite SSE. (Idempotencia/anti-doble-envío básica.)
   - `POST /conversaciones/{id}/tomar` → takeover manual: estado→`humano` aunque el bot no haya escalado (pausa el bot). Reusa la lógica de pausa existente.
   - (Existente) `POST /conversaciones/{id}/resolver` → estado→`bot`, retoma.
4. **SSE**: nuevo evento `conversacion_mensaje` (vía `notify_all`, acotado al tenant) para que el hilo se actualice en vivo. Reusar `conversacion_escalada/resuelta`.
5. **Listado del inbox**: extender `GET /conversaciones` para listar TODAS (no solo escaladas) con último mensaje y estado, para la columna izquierda del inbox (filtros: en humano / con bot).

**Frontend** — reescribir `TabConversaciones.jsx` como **Inbox** (layout tipo Chatwoot, ya hay referencia en `design-propuestas` aurora `.conv-wrap`):
- Columna izquierda: lista de conversaciones (avatar, nombre/teléfono, último mensaje, estado, "hace cuánto"), búsqueda y filtro por estado.
- Panel derecho: **hilo bidireccional** (burbujas cliente/bot/asesor diferenciadas) + **composer** para escribir, deshabilitado si la conversación está en `bot` con un botón "Tomar conversación" (→ `/tomar`), y "Devolver al bot" (→ `/resolver`).
- Realtime: `useRealtimeEvent(['conversacion_mensaje','conversacion_escalada','conversacion_resuelta'])` refresca lista e hilo.
- Banner claro del estado: "Bot en pausa — estás atendiendo tú" / "Bot activo".

### Seguridad / correctitud
- Solo se puede responder dentro de la ventana de 24h de WhatsApp (texto libre). Si está fuera de ventana, deshabilitar el composer y avisar (fuera de ventana solo van plantillas).
- Respeta RBAC (`routers/deps.py`): admin y vendedor con sus filtros.
- El bot NO debe correr mientras `estado=humano` (ya está; verificar que takeover manual también lo respeta).

### Criterios de aceptación
- [ ] Un cliente escribe pidiendo asesor → aparece en el inbox en vivo con su hilo.
- [ ] El asesor escribe desde el dashboard → le llega al cliente por WhatsApp; el bot queda en pausa.
- [ ] "Devolver al bot" reanuda el agente; el cliente vuelve a ser atendido por IA.
- [ ] Takeover manual (sin escalada del bot) funciona y pausa el bot.
- [ ] Migración up/down limpia; `pytest` y `npm test` verdes; aislamiento multi-tenant probado (A no ve hilo de B).

### Prompt para Claude Code (Fase 2)
```
Lee docs/plan-dashboard-agente-2026.md (Fase 2) y modules/conversaciones/{router,service,repository}.py,
migrations/tenant/versions/0009_conversaciones.py, apps/wa/agent.py (handoff/pausa), apps/wa/kapso.py
(enviar_texto), routers/events.py (notify_all/SSE), dashboard/src/tabs/TabConversaciones.jsx,
dashboard/src/components/RealtimeProvider.jsx, y design-propuestas/propuesta-aurora-clinica.html (.conv-wrap).

Construye el inbox con hand-off bidireccional (modelo Chatwoot sobre el estado bot/humano existente):
1) Migración tenant nueva conversacion_mensajes (cliente_telefono, direccion entrante/saliente, autor
   cliente/bot/asesor, texto, creada_en) up/down limpia.
2) Persistir en conversacion_mensajes cada mensaje entrante, cada respuesta del bot y del asesor en
   apps/wa/ (sin quitar el contexto Redis del LLM).
3) Endpoints (repositorio, async, acotados al tenant): GET /conversaciones/{id}/mensajes,
   POST /conversaciones/{id}/responder (valida estado=humano, manda por kapso.enviar_texto del número
   del tenant, persiste autor=asesor, emite SSE conversacion_mensaje), POST /conversaciones/{id}/tomar
   (takeover: estado→humano y pausa el bot). Extiende GET /conversaciones para listar todas con último
   mensaje y estado.
4) Reescribe TabConversaciones como inbox (lista + hilo + composer + Tomar/Devolver al bot), realtime
   por SSE, composer deshabilitado fuera de la ventana de 24h.
Respeta RBAC (routers/deps.py), zona horaria Colombia, async/await en endpoints con SSE, nada de SQL
suelto. TDD en service/repository y en el inbox; prueba aislamiento multi-tenant (A no ve hilo de B) y
migración up/down. Corre pytest -q y `cd dashboard && npm test -- --run`. Pasa engineering:code-review.
```

---

## FASE 3 — Diseño y catálogo de temas (listo para mostrar a prospectos)

**Meta:** componentes pulidos a nivel mockup (no solo paleta) y los temas por vertical listos para demo, de modo que vender = elegir tema + datos.

### Alcance
1. **Portar componentes al nivel de los mockups** (`design-propuestas/`): tarjetas KPI, agenda-grid, inbox, chips/badges, sidebar — usando las skills `design:design-system` (tokens/consistencia), `design:design-critique` (revisión por pantalla) y `design:ux-copy` (microcopy de estados vacíos, botones, banners del inbox).
2. **Catálogo de temas por vertical**: replicar el patrón `[data-tema="aurora"]` para los demás mockups (brasa/brisa/lienzo/navaja) como bloques de tokens en `index.css`, seleccionables por `branding.tema`. Cada vertical = un tema con su paleta/tipografía/forma; estructura compartida.
3. **Modo demo / seed de showcase**: un set de datos de ejemplo (citas, conversaciones con hilo, KPIs) por vertical para que una demo se vea "viva" sin cliente real. Reusar el provisionador/seed.
4. **Accesibilidad**: pasar `design:accessibility-review` (contraste, foco, targets) en cada tema antes de darlo por listo.
5. (Opcional) **Login tematizado por subdominio** cuando exista dominio propio con wildcard (hoy el login es pre-auth y no se tematiza; documentado como fuera de alcance del white-label actual).

### Archivos
`dashboard/src/index.css` (bloques de tema), `dashboard/tailwind.config.js`, componentes en `dashboard/src/components/*` y `tabs/*`, `design-propuestas/*` (referencia), seed/provisionador para el modo demo.

### Criterios de aceptación
- [ ] Cada vertical (≥3 temas) renderiza el dashboard con su paleta/tipografía/forma; estructura compartida; Punto Rojo intacto.
- [ ] Inbox y home de agente se ven a nivel mockup en light+dark.
- [ ] `design:accessibility-review` sin issues bloqueantes; `npm test` verde.
- [ ] Demo "viva" por vertical con datos de ejemplo.

### Prompt para Claude Code (Fase 3)
```
Lee docs/plan-dashboard-agente-2026.md (Fase 3), dashboard/src/index.css (bloque [data-tema=aurora]),
tailwind.config.js y los mockups design-propuestas/*.html. Usa las skills design:design-system,
design:design-critique, design:ux-copy y design:accessibility-review.
1) Lleva los componentes (KPI, agenda-grid, inbox, chips/badges, sidebar) al nivel visual de los
   mockups, manteniendo los tokens semánticos (sin colores hardcodeados).
2) Agrega los temas restantes (brasa/brisa/lienzo/navaja) como bloques [data-tema=...] en index.css,
   seleccionables por branding.tema, light+dark, sin tocar el tema base.
3) Crea un seed de "modo demo" por vertical (citas, conversaciones con hilo, KPIs) reusando el
   provisionador, para demostraciones sin cliente real.
Verifica accesibilidad por tema, corre `cd dashboard && npm test -- --run`, toma capturas de cada tema
en light+dark. Pasa engineering:code-review.
```

---

## Orden y dependencias

1. **Fase 1** (independiente) → cambia el look y la IA de inmediato.
2. **Fase 2** (depende de Fase 1 para el enlace "pendientes"→inbox, pero el backend es independiente) → el diferenciador vendible.
3. **Fase 3** (después, sobre la estructura ya estable) → pulido + catálogo de temas para vender.

Cada fase entra por su rama `feat/...`, PR a `main` con CI/tests verdes (el pre-deploy de Railway corre las migraciones), y se verifica en vivo en clinica-demo (tema aurora) sin romper Punto Rojo.
