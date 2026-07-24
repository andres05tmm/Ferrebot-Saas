# Comparativa Yuumi vs FerreBot/Melquiadez — vertical restaurantes (post Pack Restaurante)

*23 de julio de 2026. Cruza el feature-set de Yuumi (investigado en vivo el 20-jul: yuumi.co, precios, funcionalidades) contra lo que quedó mezclado en `main` (F0→F7, ADR 0032). Objetivo: saber dónde estás parado y qué integrar en la siguiente ronda.*

---

## 1. Marcador general

| Bloque | Yuumi | Tu plataforma hoy | Veredicto |
|---|---|---|---|
| Pedidos y domicilios | Tienda online + caja de domicilios | `pack_pedidos` + zonas + **recargo por plato** + kanban SSE | **Paridad, con ventaja** (ellos no toman pedidos por chat) |
| Menú con modificadores | Sí (adiciones, combos) | Sí (grupos min/max, deltas, snapshot) — F2 | **Paridad** |
| POS mostrador + caja + arqueos | Sí, incluye arqueo ciego | `ventas`+`caja`+arqueo híbrido | **Paridad** (arqueo ciego: brecha menor) |
| Mesas / salón | Mesas ilimitadas, precuenta, app meseros | Mesas + rondas + precuenta + propina + cobro idempotente — F3 | **Paridad funcional** (falta UX móvil de mesero pulida) |
| Comandas KDS | Zonas ilimitadas, pantallas | KDS por zona + estados + SSE + aviso "listo" — F4 | **Paridad** |
| Menú digital QR | Sí | Página pública + QR + deep-link WhatsApp — F5 | **Paridad** |
| Inventario + recetas | Recetas, sub-recetas, IA stock | Recetas BOM + insumos + COGS — F6 | **Paridad en v1** (sub-recetas/producciones: no) |
| Facturación DIAN / POS electrónico | Incluida | MATIAS + POS electrónico (ADR 0012/0014), flags | **Paridad** (activar en prod cuando toque) |
| Impuestos de restaurante | Sí | **INC 8% modelado** — F6 | **Paridad** |
| Canal conversacional WhatsApp | ❌ No existe | Bot que arma el pedido completo por chat, 20/20 eval, 0 alucinaciones | **VENTAJA TUYA (la grande)** |
| Onboarding | Registro + plantilla o migración pagada ($149.900) | Onboarding mágico: carta en foto → tenant provisionado | **VENTAJA TUYA** |
| IA operativa | Asistente de datos (plan Pro), IA inventario | Agente que EJECUTA (ventas, reportes, cobranza) + reportes restaurante | **VENTAJA TUYA** |
| Multi-vertical | Solo gastronomía | Ferretería + salón + restaurante + agenda con el mismo core | **VENTAJA TUYA** |

**Lectura honesta:** en el núcleo operativo del restaurante alcanzaste paridad funcional con Yuumi en un solo goal, y tienes tres ventajas que ellos no pueden copiar rápido. Donde Yuumi sigue adelante es en la **cáscara comercial y operativa madura**: impresión, tienda web transaccional, apps satélite, logística avanzada y años de pulido en UX real de restaurante.

## 2. Lo que Yuumi tiene y a ti te falta (priorizado)

### Ronda 2 — lo que la operación real exige en la primera semana

1. **Impresión térmica (comandas y precuenta).** Yuumi tiene "plugin de impresión"; las cocinas reales mezclan pantalla + papel, y la precuenta impresa es rito en Colombia. Sin esto, el piloto con Siriuss cojea el día 1. Es el gap #1.
2. **Pagos online cableados al pedido.** Yuumi integra pasarelas; tú ya tienes el frente `pagos_online` (Bre-B/Bold, ADR 0013) — falta conectarlo: link/QR de pago en la confirmación del pedido y en la precuenta, con conciliación. Es cablear, no construir.
3. **Arqueo ciego.** Feature pequeña (el cajero cuenta sin ver el esperado) que Yuumi lista y que da confianza al dueño. Barata sobre tu arqueo híbrido.

### Ronda 3 — la cáscara comercial

4. **Tienda web transaccional.** Tu menú QR es read-only con deep-link a WhatsApp (decisión correcta para v1). Yuumi vende "tienda online propia con dominio". El paso natural: checkout web sobre la misma página pública (carrito → dirección → pago online) compartiendo el motor de `pack_pedidos`. Con tu deep-link, el argumento intermedio ya es bueno: "tu menú QR donde se pide por WhatsApp".
5. **Fidelización (stickers/sellos digitales).** Yuumi la incluye en TODOS los planes ("App Sticker Digitales") — señal de que retiene clientes. Tu versión sería superior con cero apps: el sello vive en WhatsApp ("vas 4 de 6 almuerzos, el 7º va por cuenta de la casa"), automático por teléfono del cliente.
6. **UX de mesero móvil.** El dashboard PWA sirve, pero la pantalla de rondas de un mesero (mesa → 3 toques → comanda) merece su vista propia. Yuumi tiene "app de meseros"; tú puedes lograrlo como vista PWA sin app store.

### Ronda 4 — escala y logística (cuando haya varios restaurantes pagando)

7. **Sub-recetas y producciones** (Yuumi Pro/Premium): salsas madre, masa del día, centro de producción. Extensión natural del BOM de F6, esperar demanda real.
8. **Domicilios avanzados**: tracking del repartidor, app de domiciliarios, central multi-sede, rutas (Yuumi Premium / addon $39.900/mes). Backlog hasta tener un cliente con volumen.
9. **Integración Rappi** (Yuumi la cobra $99.900 único). Solo si un cliente la pide.
10. **Multi-marca / cocina oculta** sobre un mismo local. Tu DB-per-tenant lo insinúa; formalizarlo cuando llegue el caso.

### Deuda que Yuumi te recuerda (no-restaurante)
- Predicción de demanda/compras con IA ("IA para optimizar inventario ✨"): ellos lo venden, nadie lo hace bien — oportunidad de ventaja, no de paridad. Espera a tener 60-90 días de datos del piloto.

## 3. Lo que tú tienes y Yuumi no (tu artillería de venta)

1. **El pedido entra solo por WhatsApp.** Yuumi necesita que el cliente abra una web; tu cliente escribe "mándame 2 de carne asada con arroz de coco" y el bot lo resuelve con 0 alucinaciones medidas. Es EL diferenciador — dilo primero en toda demo.
2. **Onboarding en minutos, no semanas.** Yuumi cobra $149.900 por migrarte 80 productos; tú provisionas un restaurante desde la foto de la carta. Conviértelo en el momento "wow" de la venta: "mándame la foto de tu carta y en 10 minutos te muestro tu restaurante funcionando".
3. **IA que ejecuta y reporta** (no solo responde): resumen del día, ingeniería de menú margen×rotación (Yuumi no la tiene), cobranza, todo por chat.
4. **Multi-vertical real**: el mismo sistema que ya factura en una ferretería y agenda en un salón. Para un inversionista o un cliente con dos negocios, esto es oro.
5. **Cero comisiones + precio COP** — iguala la bandera de Yuumi, y puedes cobrar menos que sus $100.000-150.000/mes por PDV mientras validas.

## 4. Recomendación de secuencia

**Antes que cualquier feature nueva: el piloto real con Siriuss.** Actívales los flags, resuelve las 5 dudas de la carta con ellos y ponlos a operar una semana. El piloto va a reordenar esta lista mejor que cualquier análisis — sospecho que confirmará que impresión y pago por transferencia-con-comprobante (que ya tienes en visión) son lo urgente, y que la tienda web puede esperar.

Con ese insumo, el **goal "Restaurante Ronda 2"** sale casi solo: impresión térmica + pagos online cableados + arqueo ciego + los 2-3 dolores que Siriuss revele. Mismo formato: fases con condicionales, baseline, cero regresión.

Y en paralelo, lo comercial que ya está listo sin escribir código: graba el caso Siriuss en video (el "Mira los resultados de Henry's" de Yuumi), monta la landing de restaurantes con el pricing por PDV, y agenda las primeras demos con los datos del piloto.

---

*Fuentes Yuumi: investigación en vivo del 20-jul-2026 (yuumi.co — home, precios, funcionalidades, ecommerce) documentada en `analisis-yuumi-adaptacion-ferrebot.md`. Estado propio: reporte de cierre del goal Pack Restaurante (main ef7371b, F0→F7, ADR 0032).*
