# Goal — Restaurante Ronda 2: impresión térmica + UI-UX nivel Yuumi

> **Parte A** = plan fundamentado. **Parte B** = prompt compacto para `/goal` (<4.000 chars).
> **Parte C** = checklist de Andrés antes de lanzar.
> Contexto: Pack Restaurante F0-F7 mezclado en `main` (ADR 0032, baselines 2257+1 tests / replay
> 68.3% 0 peligrosos). Investigación: Yuumi resuelve impresión con un **plugin local**
> (Mac/Win/Linux) que "imprime en térmicas de forma automática, sin diálogos de confirmación", y
> app Android que imprime directo. Propina regulada por Ley 1935/2018 (voluntaria, sugerida máx.
> 10%, cliente informado). Sistema de diseño propio ya definido en `docs/design/DESIGN.md`; base
> frontend en ADR 0029 (TanStack Query, TS gradual, atajos POS).

---

# Parte A — Plan fundamentado

## A.1 Por qué estas dos cosas y no otras

Sin papel no hay restaurante: la cocina real mezcla pantalla (KDS) y comanda impresa, y la
precuenta impresa es rito en Colombia. Y sin UI pulida no hay venta: la profesionalidad que
transmite Yuumi es 50% diseño. Esta ronda ataca exactamente eso. Fuera de alcance (rondas
siguientes): pagos online cableados al pedido (ADR 0013), tienda web transaccional, fidelización,
logística avanzada.

## A.2 Arquitectura de impresión (la decisión técnica central → ADR 0033)

Dos patrones posibles, con recomendación:

- **(a) Puente navegador→localhost** (el clásico de la industria, tipo plugin Parzibyte/QZ Tray):
  la página del dashboard manda el trabajo por HTTP a un agente local que habla ESC/POS. Simple,
  pero **solo imprime si hay un navegador abierto con el dashboard en esa máquina**.
- **(b) Agente suscrito al backend (RECOMENDADO):** un servicio local pequeño por sede se conecta
  SALIENTE al backend (token de dispositivo por tenant), recibe trabajos de una **cola de
  impresión** y los ejecuta en ESC/POS. Imprime aunque no haya ningún navegador abierto (la
  comanda de un pedido de WhatsApp sale en cocina sola), sin problemas de firewall (conexión
  saliente), y reusa la infraestructura SSE/eventos existente. Es lo que el plugin de Yuumi
  aparenta ser por fuera, hecho mejor por dentro.

El ADR 0033 fija: patrón (b) con fallback (c) — **impresión por navegador con CSS de 80mm/58mm**
(`window.print`) para quien no ha instalado el agente; cola con estados
(`pendiente → entregado_agente → impreso | error`) e **idempotencia por trabajo** (una comanda
jamás se imprime dos veces por un reintento); mapeo impresora↔zona de comandas (la zona ya existe:
ADR 0032 D5); reimprimir desde el dashboard; agente en Python (mismo stack, `python-escpos`),
empaquetado para **Windows primero** (el mercado local), instalador simple.

## A.3 Contenido de los tickets (regulación incluida)

- **Comanda** (por zona): número/origen (Mesa 4 / WhatsApp / mostrador), hora, ítems con
  **modificadores en letra grande** ("SIN CEBOLLA" es la razón de ser de la comanda), notas, y
  cantidad agrupada. 80mm y 58mm.
- **Precuenta** (no fiscal): branding del tenant, ítems con modificadores, total, leyenda
  "Precios incluyen INC 8%" (ADR 0032 D2/D4), y bloque de propina conforme a **Ley 1935/2018**:
  "Propina sugerida (10%): $X — **voluntaria**, usted decide si la paga, aumenta o elimina".
  Nunca sumada por defecto al total (coincide con D7: la elige el cliente al pagar).
- **Comprobante de venta** (no fiscal mientras `pos_electronico` esté off): venta con método de
  pago y propina discriminada.
- Los tres se generan desde datos del pedido/venta — plantillas deterministas testeables por
  **golden test** (snapshot del buffer ESC/POS y del render texto).

## A.4 UI-UX nivel Yuumi (con lo que ya existe, no de cero)

Ya hay sistema de diseño **multi-vertical** (`docs/design/DESIGN.md`, reescrito: un sistema, una
piel por vertical vía `core/tenancy/branding_presets.py` → `/config` → variables CSS; el rojo
#C8200E es branding exclusivo de Punto Rojo) y base técnica (ADR 0029). Para restaurantes la piel
es el preset **`brasa`** (ladrillo cálido, Figtree, radio 16px). La ronda NO inventa un diseño
nuevo: **aplica el sistema con rigor a las 4 superficies del restaurante, tematizadas por tokens**
(cero colores de marca hardcodeados — regla dura del DESIGN.md §1), y les da el pulido que
transmite profesionalidad:

1. **TabPedidos (kanban):** tarjetas según DESIGN.md, badges de estado semánticos, tiempo
   transcurrido visible (pedido que lleva >X min cambia de tono), skeletons de carga, estado
   vacío con ilustración/CTA, toasts de acción.
2. **TabMesas:** grilla táctil (targets ≥44px, es tablet-first), estado por color tonal, total en
   vivo, flujo abrir→ronda→precuenta→cobrar en ≤3 toques por paso, modal de cobro con propina
   (botones 5%/10%/otro/sin propina — default SIN propina, Ley 1935).
3. **KDS:** modo oscuro **derivado de los tokens del preset activo** (DESIGN.md §5 — no un dark
   genérico), tipografía grande a distancia, aviso sonoro+flash en comanda nueva, cronómetro por
   comanda con umbral de alerta, botón "listo" gigante. Pantalla siempre-encendida sin degradar
   (reconexión SSE ya existe).
4. **Menú QR público:** la cara ante el cliente final — branding del tenant, fotos si existen,
   secciones sticky, botón "Pedir por WhatsApp" persistente, carga <2s en móvil 3G.

Gates verificables por máquina + un gate humano: `npm run build` y `typecheck` verdes, vitest
verde, **axe-core sin violaciones críticas/serias** en las 4 superficies, **Lighthouse del menú
público: performance ≥85 y accesibilidad ≥95** (móvil), y **checkpoint visual con Andrés** por
superficie (capturas antes/después; se avanza con su aprobación explícita). Pantallas nuevas o
rehechas siguen ADR 0029 (useQuery/useMutation + zod; sin migrar tabs viejos en bloque).

## A.5 Fases con condicionales de salida

### R0 — ADR 0033 + auditoría UI (checkpoint con Andrés)
ADR de impresión (§A.2: patrón b+c, cola, idempotencia, mapeo zona↔impresora, agente Windows
primero) + auditoría de las 4 superficies con el skill `design:design-critique` contra el
`DESIGN.md` multi-vertical, **con `restaurante-demo` en preset `brasa` activo** → lista priorizada
de cambios por pantalla con capturas del estado actual, incluyendo todo color hardcodeado que deba
migrar a tokens.
**Condicionales:** ADR aprobado por Andrés · auditoría con capturas presentada y priorización
aprobada · baselines re-registradas (suite completa + replay 68.3%/0 peligrosos).

### R1 — Cola de impresión (backend)
Migración tenant aditiva: `trabajos_impresion` (tipo comanda|precuenta|comprobante, payload
determinista, zona/impresora, estado, `idempotency_key` UNIQUE, reintentos, timestamps).
Generación automática: pedido confirmado → un trabajo POR comanda/zona (ADR 0032 D5); precuenta y
comprobante bajo demanda. API (`/api/v1/impresion`: cola del dispositivo, ack, reimprimir) +
eventos SSE. Flag `impresion` (dep: `pack_pedidos` o `pack_mesas` o `ventas`).
**Condicionales:** pedido confirmado con ítems de 2 zonas crea exactamente 2 trabajos (test) ·
reintento/doble-confirmación NO duplica trabajos (idempotencia UNIQUE testeada) · reimprimir crea
trabajo nuevo ligado al original · 404 sin flag · migración up/down limpia · suite verde · replay ≥ baseline.

### R2 — Agente local + fallback navegador
Agente Python (`tools/agente_impresion/` o repo-dir propio): login con token de dispositivo,
long-poll/SSE de SU cola, render ESC/POS (`python-escpos`, 80/58mm), ack/error con reintento,
config local impresora↔zona, log local. Empaquetado Windows (PyInstaller) + guía de instalación.
Fallback: vista de impresión del navegador con CSS térmico (80/58mm) para comanda/precuenta.
**Condicionales:** test de integración con impresora FALSA (captura de buffer): trabajo →
ESC/POS correcto → ack → estado `impreso` · corte de conexión a mitad de trabajo NO duplica la
impresión al reconectar · fallback navegador renderiza los 3 tickets con CSS de 80mm · binario
Windows construido en CI o localmente documentado · suite verde.

### R3 — Plantillas de tickets (golden tests)
Las 3 plantillas de §A.3 con datos de la carta Siriuss (fixture existente): comanda con
modificadores destacados, precuenta con leyenda INC y propina Ley 1935, comprobante.
**Condicionales:** golden test por plantilla × ancho (80/58mm) con la carta Siriuss ·
la precuenta NUNCA suma la propina al total (test explícito) · leyenda INC presente cuando el
tenant tiene productos `tipo_impuesto='inc'` · caracteres es-CO correctos (tildes, ñ, $ miles) · suite verde.

### R4 — UI-UX pass (4 superficies)
Lo definido en §A.4, superficie por superficie, cada una con su checkpoint visual.
**Condicionales:** por superficie: axe-core sin violaciones críticas/serias + vitest de los
componentes nuevos + capturas antes/después aprobadas por Andrés (con preset `brasa` activo) ·
**cero colores de marca hardcodeados en las 4 superficies** (gate greppeable: ningún hex de preset
ni #C8200E en sus componentes; solo `var(--color-*)`) · el mismo build renderiza correcto con
otro preset (smoke con `melquiadez`: la piel cambia sin tocar componentes) · menú público:
Lighthouse móvil performance ≥85, a11y ≥95, branding del tenant por tokens · atajos de teclado
del POS extendidos a mesas (abrir mesa, agregar, precuenta, cobrar) documentados en la UI ·
build + typecheck verdes · suite completa verde.

### R5 — Cierre
Manifiesto `restaurante-demo` con flag `impresion`; docs (`feature-flags.md`,
`plantillas-verticales.md`, runbook de instalación del agente); smoke E2E del ciclo con impresión:
pedido WhatsApp (carta Siriuss) → confirmado → 2 trabajos de comanda por zona → agente falso
imprime y ackea → mesa con precuenta impresa (fallback navegador) → cobro con propina →
comprobante. **Condicionales:** smoke E2E verde · `pytest tests/test_manifests_demo.py` verde ·
re-provisionamiento del demo verde · suite completa verde · replay ≥ baseline · docs al día.

## A.6 Riesgos

- **Hardware real:** los tests usan impresora falsa; la validación con una térmica física la hace
  Andrés con el binario (checklist Parte C). Las genéricas ESC/POS chinas a veces difieren en
  comandos de corte/acentos — el agente debe tener perfil "genérico" conservador por defecto.
- **UI subjetiva:** por eso cada superficie tiene checkpoint visual con Andrés — el gate humano es
  parte del diseño del goal, no un fallo.
- **Scope creep:** ni pagos, ni tienda web, ni app de mesero nativa. Ronda 3.

---

# Parte B — Prompt para pegar en `/goal` (compacto)

```
# Misión
Restaurante Ronda 2 en ferrebot-saas: impresión térmica profesional (comandas, precuenta,
comprobante) + pulido UI-UX de las 4 superficies del restaurante al nivel de Yuumi.

# Fuente única de verdad
TODO el plan vive en docs/goal-restaurante-ronda2.md — léelo COMPLETO antes de tocar nada y
trátalo como contrato: arquitectura de impresión (§A.2, patrón agente-suscrito + fallback
navegador), contenido regulado de tickets (§A.3, propina Ley 1935: voluntaria, sugerida 10%,
jamás sumada por defecto), alcance UI-UX (§A.4: sistema MULTI-VERTICAL de docs/design/DESIGN.md —
preset `brasa` para restaurantes, cero colores de marca hardcodeados, solo tokens CSS de
branding_presets.py — y ADR 0029), fases R0→R5 con condicionales (§A.5) y riesgos (§A.6). Contexto previo: ADR 0032 (pack restaurante),
fixture docs/fixtures/carta-siriuss/carta.yaml, reglas en CLAUDE.md y .claude/rules/.

# Método (resumen; ante cualquier duda, manda el .md)
1. Primer paso: milestone "Restaurante Ronda 2" en GitHub + un issue por fase (R0..R5) con sus
   condicionales como checklist.
2. Fases EN ORDEN, una rama y un PR por fase. RED→GREEN→REFACTOR. Tras cada fase: suite completa
   + replay ferretería. NO avanzar con un condicional en rojo ni si el replay baja de la baseline
   (68.3%, 0 peligrosos).
3. R0 es checkpoint con Andrés (ADR 0033 + auditoría UI con capturas). En R4, CADA superficie
   requiere su aprobación visual con capturas antes/después — PARAR y mostrar.
4. Guardarraíles: idempotencia UNIQUE en trabajos de impresión (jamás doble impresión),
   anti-alucinación, nada mueve stock/caja sin movimiento, aislamiento multi-tenant, migraciones
   aditivas, flags con dependencias, axe/Lighthouse como gates de UI.

# Condición de término
Se cumple, verificada, la fase R5 de docs/goal-restaurante-ronda2.md §A.5: smoke E2E con
impresión (pedido WhatsApp carta Siriuss → 2 comandas por zona → agente falso imprime y ackea →
precuenta → cobro con propina → comprobante), golden tests de las 3 plantillas × 2 anchos,
binario Windows del agente documentado, 4 superficies con axe sin violaciones críticas/serias y
capturas aprobadas por Andrés, menú público Lighthouse ≥85/≥95, manifiesto demo con flag
`impresion`, suite completa verde, replay ≥ baseline, migraciones up/down limpias, ADR 0033 y
docs al día. Al cerrar cada fase reporta: condicionales verde/rojo, números de suite y replay, y
qué sigue.
```

---

# Parte C — Checklist de Andrés ANTES de lanzar

1. **Repo privado** (si no lo has hecho ya) y **rotación de secretos** que hayan tocado el
   historial mientras estuvo público. No lances el goal con eso pendiente.
2. **CI disponible**: minutos de Actions repuestos (1 ago) o presupuesto puesto — la Ronda 2
   vuelve a correr ~6 ciclos de CI. Alternativa: mezclar con suite local como en la Ronda 1.
3. **Tag de respaldo**: `git tag pre-ronda2 && git push --tags` con `main` en verde.
4. **Consigue la impresora térmica física** para la validación final del agente: cualquier
   térmica **ESC/POS de 80mm** sirve (Epson TM-T20 o genérica USB; en Colombia ~$200.000-400.000
   COP). No bloquea el goal (los tests usan impresora falsa), pero sin ella no puedes validar el
   binario real ni grabar el demo comercial.
5. **Prepárate para los checkpoints visuales**: en R0 apruebas el ADR + la priorización UI, y en
   R4 apruebas cada superficie con capturas. Es lo que garantiza el "toque Yuumi" — resérvale
   atención de verdad; si algo no te gusta, dilo ahí (es barato) y no después del merge.
6. **Referencias visuales**: si hay pantallas de Yuumi que te gustan (de sus videos/tutoriales),
   guarda capturas en `docs/design/referencias-yuumi/` — le dan al goal un norte visual concreto
   en la auditoría de R0.

---

*Fuentes de investigación: [plugin de impresión Yuumi](https://yuumi.co/en/descargar-plugin-impresion-pos/?currency=CO) (verificado en navegador: agente local Mac/Win/Linux, impresión automática sin diálogos, Android imprime directo) · [patrón HTTP→ESC/POS local](https://parzibyte.me/blog/posts/guia-inicio-rapido-impresora-termica/) · [Ley 1935 de 2018 — propina](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=87873) y [guía práctica](https://www.siigo.com/blog/obligaciones-fiscales/propina-colombia-ley-1935/) · repo: `docs/design/DESIGN.md`, ADR 0029, ADR 0032.*
