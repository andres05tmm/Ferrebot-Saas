# Goal — Pack Restaurante completo (paridad Yuumi) sobre ferrebot-saas

> **Parte A** = plan fundamentado (léelo tú y Claude Code). **Parte B** = prompt listo para pegar en `/goal`.
> **Parte C** = checklist de Andrés ANTES de lanzar el goal.
> Basado en el análisis competitivo de Yuumi (sesión Cowork 20-jul-2026) y en el estado real del repo:
> ADR 0016 (`pack_pedidos`), ADR 0021 (features finas POS), ADR 0022 (cobro→venta), ADR 0014 (documento
> por venta), plantilla `restaurante-demo` (`plantillas-verticales.md`), `docs/goal-prompt.md` (formato de goal previo).

---

# Parte A — Plan fundamentado

## A.1 Dónde estamos vs Yuumi (brecha real, no imaginada)

Lo que Yuumi ofrece y **ya tenemos** (no se construye, se reusa):

| Capacidad Yuumi | Ya existe en ferrebot-saas |
|---|---|
| Pedidos + domicilios con zonas y estados | `pack_pedidos` (ADR 0016): motor determinista, kanban SSE, zonas, idempotencia |
| POS de mostrador, caja, arqueos | features finas `ventas`/`caja`/`inventario` (ADR 0021) + arqueo híbrido |
| Facturación electrónica / POS electrónico | ADR 0012/0013/0014, MATIAS, `pos_hook` `encolar_cierre_pos` |
| Inventario, compras, proveedores | `inventario` + `pedidos_proveedor` (ADR 0031) + COGS (ADR 0025) |
| Tienda/canal digital | `canal_whatsapp` (Kapso) + `pack_faq` + `pack_ventas` |
| Multi-sede / multi-marca | DB-per-tenant + manifiesto (ADR 0007) |
| Offline | PWA + cola offline (ADR 0004) |

Lo que Yuumi tiene y **nos falta** (esto ES el goal, en orden de dependencia):

1. **Pedido → venta** (el propio ADR 0016 lo declara "v2" pendiente): hoy el kanban opera el ciclo pero el pedido nunca se convierte en venta POS (stock, caja, documento). Sin esto el restaurante no cuadra caja.
2. **Modificadores de menú**: "sin cebolla", "término medio", "adición queso", combos. El pedido de restaurante es inviable sin esto; hoy `pedido_items` solo tiene producto+cantidad.
3. **Mesas / salón**: orden abierta por mesa, ítems incrementales, precuenta, cobro que cierra en venta (reusa el patrón ADR 0022), propina.
4. **Comandas KDS**: zonas de cocina (parrilla/bar), vista de cocina con estado por ítem, "listo" en tiempo real. El kanban actual es de domicilios; el KDS es otra vista sobre los mismos datos.
5. **Menú digital QR**: página pública read-only del menú por tenant con deep-link a WhatsApp.
6. **Recetas (BOM)**: vender un plato descuenta insumos y calcula costo del plato (extiende `inventario` + ADR 0025).
7. **IA de restaurante**: upsell acotado al catálogo, resumen del día restaurantero, ingeniería de menú básica (margen × rotación).

**Fuera de alcance de este goal** (backlog, no lo intentes): app nativa de repartidores, integración Rappi, rutas inteligentes, propinas compartidas/nómina, DIAN real en producción (queda detrás de flags y mocks — el MVP es operativo sin fiscal, decisión de Andrés).

## A.2 Principios que gobiernan el diseño (los del repo, aplicados)

- **Vertical nuevo sin tocar el runtime** (consecuencia declarada del ADR 0016): todo entra como datos + motor determinista + herramientas + flags. La IA sigue siendo interfaz; los precios, totales, stock e impuestos son deterministas.
- **Flags con dependencias** (ADR 0021): proponemos `pack_mesas → {ventas}`, `kds → {pack_pedidos o pack_mesas}`, `menu_qr → {ventas}`, `recetas → {inventario}`. Los nombres finales los fija el ADR nuevo (ver F0) respetando `core/tenancy/catalogo.py`.
- **Invariantes intocables**: nada mueve stock sin movimiento de inventario ni caja sin venta/movimiento; idempotencia con clave UNIQUE; migraciones tenant **aditivas** y NULL-safe (los demás verticales comparten esquema).
- **Anti-alucinación**: modificador o producto inexistente → el bot pregunta, nunca inventa (rieles R1/R2 existentes).
- **Punto Rojo no se toca**: cero regresión en el replay de ferretería; el corpus real es la red de seguridad.

## A.3 Fases con condicionales de salida (el corazón del /goal)

Cada fase = rama + PR + migración aditiva si aplica + tests RED-GREEN + condicionales verificables por máquina. **No se avanza de fase con una condición en rojo.**

### F0 — ADR + baseline (sin código de producto)
Escribir **ADR 0032 — Pack Restaurante** (usa el skill `engineering:architecture`): nombres definitivos de flags, modelo de modificadores (grupos/opciones con delta de precio, snapshot en el ítem), modelo de mesas/orden abierta, zonas KDS. **Decisiones ya tomadas por Andrés (no re-preguntar, solo diseñar):** (a) el **impoconsumo 8% SE MODELA** — todos los restaurantes lo llevan; el catálogo hoy solo modela IVA 0/5/19, así que el ADR define `tipo_impuesto` (o equivalente) por producto, con IVA y INC coexistiendo porque el esquema es compartido con ferreterías; (b) **propina**: solo aplica en venta de salón/mostrador (NUNCA en domicilio), siempre opcional y elegida por el cliente al pagar; (c) **insumo insuficiente** en recetas: alerta, no bloquea (pendiente de ratificar en el ADR). El ADR también resuelve las DUDAS D1-D5 y las INFERENCIAS I1-I4 de `docs/fixtures/carta-siriuss/carta.yaml` preguntándole a Andrés en el mismo checkpoint, y decide cómo modelar el **recargo por plato de zona** (Bocagrande +$1.000/plato — hoy `zonas_domicilio` es tarifa plana por pedido).
**Condicionales:** ADR escrito y aprobado por Andrés (PARAR y mostrar) · DUDAS del fixture resueltas y registradas en el ADR · suite completa verde documentada como baseline (número exacto de tests) · replay ferretería ejecutado y baseline registrada.

### F1 — Pedido → venta (cierra ADR 0016 v2)
Acción "Convertir en venta" desde el kanban/API: crea la venta con los ítems del pedido (snapshot), `idempotency_key = "pedido-venta:{pedido_id}"`, vínculo `pedidos.venta_id` (FK UNIQUE, misma transacción, `SELECT … FOR UPDATE` — patrón calcado de ADR 0022 D3), descuenta stock SOLO de productos con inventario activo, cuadra caja por `ventas_efectivo`, invoca `encolar_cierre_pos` best-effort fuera de la transacción (ADR 0014).
**Condicionales:** test de idempotencia (2 conversiones concurrentes → 1 venta, replay=true) · test invariantes (stock solo cambia vía movimiento; arqueo cuadra con la venta) · E2E HTTP: pedido confirmado → convertir → venta consultable y pedido `entregado` · pedido cancelado/ya convertido → 409/replay · suite verde.

### F2 — Modificadores de menú
Migración tenant: grupos de modificadores por producto (`min/max` selección, opciones con `delta_precio`), snapshot completo en `pedido_items` (nombre+delta al momento). Motor: `armar_pedido` acepta modificadores y calcula total determinista. Tools del bot actualizadas; el buscador sugiere, nunca inventa. Kanban y conversión F1 muestran/copian modificadores.
**Condicionales:** E2E conversacional simulado: "2 hamburguesas, una sin cebolla y con adición de queso, y una limonada" → pedido con 3 ítems, modificadores correctos y total exacto verificado contra catálogo · modificador inexistente ("sin kriptonita") → el bot pregunta, no registra · conversión a venta conserva los modificadores en la descripción · migración `upgrade/downgrade` limpia · suite verde.

### F3 — Mesas y salón
Migración tenant: `mesas` (nombre/zona/activo) + orden abierta por mesa (reusa `pedidos` con `origen=mesa` y estado propio, o entidad nueva — lo fija el ADR). Flujo: abrir mesa → agregar ítems incremental → precuenta (imprimible/compartible) → cobrar (efectivo/transferencia/datáfono; **propina opcional elegida por el cliente al pagar, como línea varia — solo existe en salón/mostrador, jamás en domicilio**, decisión de Andrés) → cierra en venta por el puente F1. Dashboard: TabMesas (grilla de mesas con estado y total en vivo, SSE).
**Condicionales:** E2E: abrir mesa → 2 rondas de ítems → precuenta con total correcto → cobro → venta única idempotente y mesa liberada · dos meseros agregando a la misma mesa concurrentemente no duplican ítems · propina no altera el total de productos y queda discriminada · flag `pack_mesas` gatea router+tab (404/oculto sin flag) · suite verde.

### F4 — Comandas KDS
Config de zonas de comandas (parrilla, bar, …) y ruteo producto→zona. Vista KDS (ruta dashboard tipo `/kds`) por zona: cola de comandas con ítems, estado por comanda (`pendiente → en_preparacion → listo`), SSE en vivo, avisa al canal del pedido cuando está listo. Alimentada por pedidos confirmados (WhatsApp) y por mesas (F3).
**Condicionales:** pedido confirmado con ítems de 2 zonas genera comandas separadas por zona · transición de estados válida y auditada; SSE recibido en test (patrón `useRealtime`) · "listo" dispara notificación al teléfono del pedido (mock del canal) · KDS invisible sin flag `kds` · suite verde.

### F5 — Menú digital QR (público)
Página pública read-only por tenant (slug) con el menú (secciones, precios, modificadores, branding del tenant), sin auth, **sin exponer datos sensibles** (solo catálogo activo), con deep-link a WhatsApp para pedir. Generación del QR desde el dashboard.
**Condicionales:** test público: la página responde sin token y NO contiene datos de otros tenants ni campos internos (test de aislamiento explícito) · producto desactivado no aparece · flag `menu_qr` la gatea · Lighthouse/render básico verificado (carga sin JS de dashboard) · suite verde.

### F6 — Recetas (BOM) e insumos
Migración tenant: receta por producto (insumo, cantidad, unidad — compatible con fracciones existentes). Al convertir pedido/mesa en venta (F1/F3), los productos con receta descuentan **insumos** vía movimientos de inventario (el plato mismo no lleva stock); costo del plato = suma de costos de insumos (reusa COGS promedio ponderado, ADR 0025).
**Condicionales:** venta de un plato con receta genera movimientos de inventario de TODOS sus insumos y ningún movimiento del plato · venta de producto sin receta se comporta como hoy (regresión cero en ferretería, replay verde) · costo del plato calculado y visible en reportes · insumo insuficiente NO bloquea la venta pero alerta (política del ADR) · flag `recetas` (dep. `inventario`) · suite verde.

### F7 — IA de restaurante + cierre
(1) Upsell determinista-acotado: el bot puede sugerir 1 complemento del catálogo real (config por tenant, riel: solo productos existentes, nunca inventa promos). (2) Resumen del día restaurantero (ventas por canal mesa/domicilio/mostrador, top platos, tiempo medio de ciclo). (3) Reporte ingeniería de menú: margen (F6) × rotación por plato. (4) Actualizar `plantillas-verticales.md`, `feature-flags.md`, `restaurante-demo.manifest` con los flags nuevos y el catálogo demo con modificadores.
**Condicionales:** eval de function-call accuracy del bot restaurante en verde (mínimo: corpus curado de 20 mensajes de pedido con modificadores, ≥90% de acierto de herramienta+args, 0 alucinaciones de precio/producto) · `pytest tests/test_manifests_demo.py` verde con la plantilla actualizada · provisionar `restaurante-demo` desde cero termina con smoke verde · docs actualizados · suite completa verde.

### Definición de Hecho global (fin del goal)
- **Demo E2E completa** (test automatizado): cliente WhatsApp simulado pide 2 hamburguesas (1 sin cebolla + adición queso) y una limonada a domicilio → pedido confirmado con zona → comandas en KDS por zona → "listo" → convertir en venta → insumos descontados por receta, caja cuadrada en el arqueo, documento fiscal encolado (mock) → estado `entregado`.
- Paralelo en salón: mesa con 2 rondas → precuenta → cobro con propina → venta idempotente.
- `restaurante-demo` re-provisionado desde manifiesto con todos los flags nuevos, smoke verde.
- Suite completa verde · replay ferretería ≥ baseline (0 regresión Punto Rojo) · migraciones aditivas up/down limpias · docs y ADR al día.

## A.4 Riesgos señalados de antemano

- **Impoconsumo 8%** es la decisión de modelado más cara (afecta catálogo, venta y futuro fiscal). Se decide en F0 con ADR, no a mitad de camino.
- **Orden abierta de mesa vs `pedidos`**: reusar la tabla es tentador pero puede forzar estados híbridos; el ADR debe elegir con trade-offs explícitos (checkpoint con Andrés).
- **KDS y SSE bajo carga**: el patrón SSE existe; el riesgo es UX de cocina (pantalla siempre encendida, reconexión). Mantener v1 simple.
- **Scope creep hacia Yuumi completo**: repartidores, Rappi, multi-marca avanzado NO entran. El goal termina en la Definición de Hecho, no en paridad total.

---

# Parte B — Prompt para pegar en `/goal` (Claude Code)

> `/goal` limita la condición a 4.000 caracteres → este prompt es deliberadamente corto y delega
> TODO el detalle a este mismo archivo, que es la fuente única de verdad.

```
# Misión
Construir el PACK RESTAURANTE completo de ferrebot-saas (paridad Yuumi: pedidos WhatsApp con
modificadores + POS + mesas + comandas KDS + menú QR + recetas).

# Fuente única de verdad
TODO el plan vive en docs/goal-pack-restaurante.md — léelo COMPLETO antes de tocar nada y trátalo
como contrato: brecha vs Yuumi (§A.1), principios (§A.2), fases F0→F7 con condicionales de salida
(§A.3), riesgos (§A.4), decisiones ya tomadas por Andrés y Definición de Hecho global. La carta
real del E2E es docs/fixtures/carta-siriuss/carta.yaml (con DUDAS D1-D5 e INFERENCIAS I1-I4 a
resolver con Andrés en F0). Reglas del repo: CLAUDE.md y .claude/rules/.

# Método (resumen; ante cualquier duda, manda el .md)
1. Primer paso: crear en GitHub el milestone "Pack Restaurante" y un issue por fase (F0..F7) con
   sus condicionales como checklist; cerrarlos al cerrar cada fase.
2. Fases EN ORDEN, una rama y un PR por fase. RED→GREEN→REFACTOR. Tras cada fase: suite completa
   + replay de ferretería. NO avanzar con un condicional en rojo ni si el replay baja de la
   baseline registrada en F0.
3. F0 es checkpoint con Andrés (ADR 0032 + DUDAS del fixture). PARAR en los puntos que el .md
   marca; NO re-preguntar las decisiones ya tomadas (impoconsumo 8% se modela; propina solo
   salón/mostrador, opcional; carta = Siriuss).
4. Guardarraíles no negociables del .md: anti-alucinación, nada mueve stock/caja sin movimiento,
   idempotencia UNIQUE, aislamiento multi-tenant, migraciones aditivas, flags con dependencias.

# Condición de término
Se cumple, verificada, la "Definición de Hecho global" de docs/goal-pack-restaurante.md §A.3:
demo E2E WhatsApp→KDS→venta con la carta Siriuss (recetas descontando insumos, caja cuadrada,
cierre fiscal mock encolado) + demo salón mesa→precuenta→cobro con propina + restaurante-demo
re-provisionado desde manifiesto con smoke verde + suite completa verde + replay ferretería ≥
baseline + migraciones up/down limpias + ADR 0032 y docs actualizados. Al cerrar cada fase
reporta: condicionales verde/rojo, números de suite y replay antes/después, y qué sigue.
```

## Versión extendida (referencia — NO cabe en /goal, el goal la lee aquí)

```
# Misión
Construye el PACK RESTAURANTE completo de ferrebot-saas hasta paridad operativa con Yuumi
(pedidos WhatsApp + POS + mesas + comandas KDS + menú QR + recetas), trabajando por fases con
condicionales de salida verificables. No terminas hasta cumplir la Definición de Hecho.

# Contexto (léelo ANTES de tocar código)
- Plan completo, fases y condicionales: docs/goal-pack-restaurante.md (Parte A) — EMPIEZA AQUÍ.
- Carta REAL de referencia (fixture del E2E): docs/fixtures/carta-siriuss/carta.yaml — extraída de
  la carta del tenant Siriuss bajo el contrato ADR 0011, con DUDAS/INFERENCIAS pendientes que
  resuelves con Andrés en el checkpoint de F0. El menú demo y los tests E2E usan ESTA carta
  (plato fuerte $19.000 con 10 proteínas y acompañantes; menú especial $21.000; sopa $14.000;
  recargo Bocagrande +$1.000/plato), no datos inventados.
- Estado del vertical hoy: docs/adr/0016-pack-pedidos.md (pack_pedidos; su "v2" pendiente es tu F1),
  docs/adr/0021-particion-pack-pos.md (features finas), docs/adr/0022-cobro-cita-venta.md (patrón
  puente→venta que debes calcar), docs/adr/0014-documento-por-venta.md (cierre fiscal best-effort),
  docs/plantillas-verticales.md (restaurante-demo), docs/feature-flags.md (catálogo de flags).
- Reglas del repo: CLAUDE.md y .claude/rules/ (multitenancy, testing, seguridad, workflow).
- Issues: repo andres05tmm/Ferrebot-Saas (gh CLI, ver docs/agents/issue-tracker.md).

# Método
- PRIMER PASO: crea tú el milestone "Pack Restaurante" en GitHub (gh CLI) y un issue por fase
  (F0..F7) con sus condicionales como checklist; ve cerrándolos al cerrar cada fase.
- Fases F0→F7 del plan, EN ORDEN. Una rama y un PR por fase (feat/restaurante-f1-..., etc.).
- Cada fase: RED (tests de los condicionales) → GREEN → REFACTOR → correr suite completa →
  correr replay ferretería → comparar contra baseline → si algo baja, arreglar antes de avanzar.
- NO avances de fase con un condicional en rojo. NO reescribas lo que funciona: pack_pedidos,
  ventas, caja e inventario se EXTIENDEN, no se reemplazan.
- Migraciones tenant SIEMPRE aditivas y NULL-safe (el esquema es compartido por todos los verticales).
- Usa subagentes de revisión (seguridad, performance) por fase y los skills engineering:* según la tarea.
- Commits `tipo: descripción`; PR con resumen + plan de prueba; CI verde antes de mezclar.

# Guardarraíles NO negociables (romper uno = bug crítico, bloquea la fase)
- ANTI-ALUCINACIÓN: el bot NUNCA inventa productos, modificadores, precios ni totales. Todo dato
  viene de herramientas deterministas. Ambigüedad → preguntar (rieles R1/R2).
- Nada mueve stock sin movimiento de inventario, ni caja sin venta/movimiento. Idempotencia con
  clave UNIQUE en toda operación crítica nueva (pedido→venta: "pedido-venta:{id}").
- Aislamiento multi-tenant en TODO (incluida la página pública del menú: test de aislamiento explícito).
- Flags con dependencias validadas (ADR 0021); routers 404 sin flag; tabs ocultos sin flag.
- Zona horaria Colombia, secretos jamás en código/git/logs, datos solo vía repositorios,
  logging estructurado con tenant_id/request_id.
- CERO regresión en ferretería: el replay de Punto Rojo nunca baja de la baseline de F0.

# Decisiones YA tomadas por Andrés (no re-preguntar)
- Impoconsumo 8%: SE MODELA (tipo de impuesto por producto; IVA y INC coexisten en el esquema).
- Propina: solo salón/mostrador, nunca domicilio, opcional y elegida por el cliente al pagar.
- La carta de referencia es la de Siriuss (docs/fixtures/carta-siriuss/carta.yaml).

# PARAR y preguntar a Andrés (no decidas solo)
- El ADR 0032 completo antes de escribir código de producto (F0 es un checkpoint contigo),
  incluyendo las DUDAS D1-D5 e INFERENCIAS I1-I4 del fixture de la carta Siriuss.
- El modelado de orden-de-mesa (reusar `pedidos` vs entidad nueva) si los trade-offs no son claros.
- El recargo por plato de zona (Bocagrande) si cambiar `zonas_domicilio` tiene efectos colaterales.
- Nombres definitivos de flags si difieren de los propuestos (pack_mesas, kds, menu_qr, recetas).
- Cualquier operación destructiva, que toque datos de Punto Rojo, o que emita documento fiscal real.

# Definición de Hecho (cuándo terminaste)
La de docs/goal-pack-restaurante.md §A.3 "Definición de Hecho global", completa: demo E2E
WhatsApp→KDS→venta con recetas y caja cuadrada; demo salón mesa→precuenta→cobro; restaurante-demo
re-provisionado desde manifiesto con smoke verde; suite completa verde; replay ferretería ≥ baseline;
migraciones up/down limpias; ADR 0032 + docs actualizados (plantillas-verticales, feature-flags).

Al cerrar cada fase reporta: qué cambió, condicionales con su estado (verde/rojo), números de la
suite y del replay antes/después, y qué sigue.
```

---

# Parte C — Checklist de Andrés ANTES de lanzar el `/goal`

1. **Deja `main` en verde y respáldate.** Con Postgres y Redis arriba: `pytest` completo debe pasar. Anota el número de tests (será la baseline de F0). Luego `git tag pre-pack-restaurante && git push --tags`. Si hay ramas a medio mergear, resuélvelas ANTES — el goal no debe heredar deuda de merge.
2. **Corre el replay de ferretería una vez** y guarda el resultado (la baseline anti-regresión que el goal comparará): el comando exacto está en `docs/goal-prompt.md` §Método.
3. **Verifica que este archivo esté en el repo** como `docs/goal-pack-restaurante.md` (lo dejé guardado ahí). Claude Code lo lee como fuente del plan.
4. ~~Milestone en GitHub~~ — **lo crea Claude Code** como primer paso del goal (ya está en el prompt de la Parte B).
5. **Carta real: LISTA.** La carta de Siriuss ya está extraída y validada con el normalizador del skill en `docs/fixtures/carta-siriuss/carta.yaml` (cobertura 17/17 filas). **Solo falta que guardes la foto original** como `docs/fixtures/carta-siriuss/carta-siriuss.jpg`. En el checkpoint de F0, Claude Code te preguntará las 5 DUDAS del fixture (¿cuántos acompañantes?, ¿sopa incluida?, ¿bebida?, ¿precio con o sin INC?, ¿tarifa base de domicilio?) — si puedes, pregúntale eso a Siriuss antes de lanzar.
6. **Decisiones de política: TOMADAS** (registradas en F0 y en el prompt): impoconsumo 8% se modela; propina solo en salón/mostrador, opcional, elegida por el cliente; insumo insuficiente = alerta (ratificar en el ADR).
7. **Nada de credenciales nuevas**: el MVP es sin fiscal real (MATIAS queda mockeado/flag off) y el canal WhatsApp se testea simulado. Solo si quieres el smoke manual final por WhatsApp real, ten a mano un número de pruebas de Kapso — no es bloqueante.
8. **Decide cuándo lo lanzas**: el goal es largo (7 fases). Idealmente lánzalo cuando puedas responder los 2-3 checkpoints de "PARAR" del primer día (el ADR de F0 es el más importante).
