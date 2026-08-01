# Rediseño del tab "Resultados financieros" — Punto Rojo

> Investigación de referencias + propuesta de estructura y datos.
> Fecha: 2026-07-29 · Alcance: `dashboard/src/tabs/TabResultados.jsx` y sus endpoints en `modules/reportes/`.

---

## 0. Diagnóstico rápido de lo que hay hoy

`TabResultados.jsx` (359 líneas) tiene tres sub-tabs sobre un rango de fechas:

| Sub-tab | Endpoint | Qué muestra |
|---|---|---|
| **Resultados** | `/reportes/resultados` + `/reportes/proyeccion-caja` | 5 métricas planas (ingresos, costo, U. bruta, gastos, U. neta) + barras de composición + proyección de cierre de mes |
| **Flujo de dinero** | `/reportes/flujo-dinero` | Entró / Salió / Neto + dos listas de desglose |
| **Margen por producto** | `/reportes/margen-productos` | Tabla con badge de cobertura de costo |

### Lo que ya está bien y hay que conservar

1. **El badge de `cobertura_pct`.** Es exactamente el patrón que usa Shopify (separar "ventas con costo registrado" de "ventas sin costo"). Muy pocos POS colombianos lo hacen. Es un activo de credibilidad — extiéndelo a los KPIs de arriba, no solo a la tabla.
2. **Que el fiado NO cuente como entrada de dinero**, y que se muestre aparte como cartera. Conceptualmente correcto y es la distinción caja/causación bien resuelta.
3. **La separación gasto vs. retiro vs. inversión vs. pago de deuda** (`ResumenGastos`, ADR 0071). Evita el error #10 de la lista de trampas (mezclar plata del dueño con gasto operativo).
4. **El punto de equilibrio ya calculado en backend** (`punto_equilibrio_mes`, `punto_equilibrio_dia`) — hoy vive en el tab Gastos, pero pertenece aquí.
5. Gate por `isAdmin()` y re-fetch por eventos realtime.

### Los cuatro problemas del diseño actual

**P1 — No hay comparación con nada.** Cinco cifras sin contexto. "Utilidad neta $14.500.000" no le dice al dueño si va bien o mal. Toda métrica necesita un delta contra un periodo nombrado. El backend ya sabe hacerlo (`gasto_periodo_anterior` en `ResumenGastos`); falta hacerlo en `EstadoResultados`.

**P2 — El gráfico de barras no explica nada.** Poner Ingresos / Costo / U.bruta / Gastos / U.neta como cinco barras independientes mezcla magnitudes que no son comparables entre sí (U. neta y ingresos en la misma escala hace que la utilidad se vea como una rayita). Ese gráfico debe ser un **waterfall**, que es la forma correcta de mostrar una descomposición aditiva.

**P3 — Cinco KPIs de igual peso.** No hay métrica héroe. El dueño de ferretería tiene una sola pregunta primaria: *"¿este mes gané o perdí, y por qué?"*. La jerarquía debe responderla en 5 segundos.

**P4 — Duplicación con `TabEstadosFinancieros`.** Hay dos "Estados de resultados" en el producto: el gerencial (`/reportes/resultados`, agregación directa) y el del ledger de doble partida (`/contabilidad/estado-resultados`, ADR 0030, gate `contabilidad_ledger`). **Van a dar números distintos** y el dueño va a perder la confianza en ambos. Hay que decidir la relación entre los dos (ver §5).

---

## 1. De dónde influenciarse — referencias priorizadas

### Tier 1 — copiar la estructura

**Lightspeed Retail X-Series — Retail Dashboard**
https://x-series-support.lightspeedhq.com/hc/en-us/articles/25533720653211-Using-the-retail-dashboard

Es la referencia más cercana a tu caso: retail con inventario, negocio pequeño. Sus 8 widgets son casi tu lista ideal (Revenue sin impuestos, Sale count, Gross profit = revenue − COGS, Discount amount, Discount %, Average sale value, Items per sale). **Lo crítico a copiar: cada widget tiene "View report" que abre el detalle arrastrando todos los filtros activos.** Un drill-down que pierde el filtro de periodo es peor que no tener drill-down.

**Toast POS — Reporting Dashboard**
https://support.toasttab.com/en/article/How-to-Use-the-Toast-Reporting-Dashboard

Modelo limpio de "4 KPIs → drill-down". Tres cosas concretas:
- Comparador con 3 presets: periodo anterior / mismo periodo año pasado / hace 2 años.
- **El día/periodo en curso se sombrea en gris** para señalar dato parcial. Detalle que casi nadie implementa y que evita que el dueño compare julio incompleto contra junio completo.
- Mapa explícito métrica → reporte destino, sin excepciones.

**Shopify — Finance Summary Report**
https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/report-types/default-reports/finances-report

La cadena `Ventas brutas − Descuentos − Devoluciones = Ventas netas` como componente propio, con cada renglón enlazado a su reporte. **Y el patrón de cobertura de costo que tú ya tienes** — Shopify lo eleva al nivel del KPI, no lo esconde en una tabla.

### Tier 2 — vocabulario y expectativas del contador colombiano

**Siigo — análisis vertical y horizontal**
https://siigopyme.portaldeclientes.siigo.com/basedeconocimiento/informes-financieros-analisis-financiero/

Dos columnas que el contador colombiano espera y que son baratas de implementar:
- **Vertical:** cada línea como **% de ventas**. Un ferretero entiende "el arriendo es el 8% de mis ventas" mucho mejor que "$4.200.000".
- **Horizontal:** columnas **Δ$ y Δ%** contra el periodo comparado.

**Alegra — taxonomía de reportes + POS ferreterías**
https://ayuda.alegra.com/int/explicaci%C3%B3n-de-los-reportes-inteligentes · https://www.alegra.com/colombia/pos/ferreterias/

Vocabulario exacto: *Estado de Resultados* (no "P&L"), *Costo de ventas*, *Utilidad bruta / operacional / neta*. Y la categoría **"reportes de trabajo" pensada para el contador**: un botón "Exportar para mi contador" que entrega Excel con estructura de cuentas. Alto valor percibido, bajo costo.

**Loggro — drill-down contable**
https://loggro.com/software-contable/software-para-reportes-contables-y-financieros/

Filosofía "de lo general a lo particular": **máximo 3 saltos hasta el documento origen**, sin callejones sin salida.

**Bsale — KPIs LatAm**
https://ayuda.bsale.io/support/solutions/articles/151000213248-reportes-de-ventas

Sus 5 KPIs de vista rápida (venta total, margen, n° de ventas, unidades, ticket promedio) son el set LatAm probado. Y el **toggle bruto/neto configurable** por el IVA — que en Colombia es el error #1 que rompe la confianza.

**Xero — Business Snapshot**
https://www.xero.com/us/accounting-software/analytics/snapshot/

La lección de arquitectura: Xero separa deliberadamente **Dashboard** (operación diaria) de **Business Snapshot** (tendencia, rentabilidad, estrategia). En tu producto: `/hoy` es el dashboard, `/resultados` es el snapshot. Si mezclas, el dueño no encuentra ninguno. Xero además pone **DSO y DPO** como KPIs de primer nivel — correcto para un negocio con crédito, que **no** es el caso de Punto Rojo (ver el recuadro de Tier 1).

### Tier 3 — principios de diseño

| Fuente | Regla accionable |
|---|---|
| [ClearPoint](https://www.clearpointstrategy.com/blog/kpi-dashboard-best-practices) | 5–9 piezas de información procesables a la vez. Jerarquía: **3–5 métricas nivel 1**, 8–12 nivel 2, el resto detrás del drill-down. |
| [IGC — dashboard layout](https://www.intelligentgraphicandcode.com/design/dashboard-design/dashboard-layout) | Pirámide invertida: Estado → Tendencias → Detalle. **Test de los 5 segundos**: mira la fila superior 5s, quítala, ¿sabes si algo está mal? |
| [IBCS](https://www.ibcs.com/resource/top-and-bottom-5-of-international-business-communication-standards/) (estándar ISO desde 2025) | Rojo/verde **solo** para varianzas. Escenarios por relleno, no por color: **sólido = real, contorno = presupuesto, rayado = proyección**. Y "Mensaje ausente" es un defecto: cada widget debe titular la conclusión, no la categoría. |
| [Tableau — daltonismo](https://www.tableau.com/blog/examining-data-viz-rules-dont-use-red-green-together) | Rojo/verde falla en ~8% de hombres. Series en azul/naranja/gris; rojo/verde solo en deltas, **siempre con flecha ▲/▼ y signo**. |
| [Polaris data viz](https://polaris-react.shopify.com/design/data-visualizations) | Barras horizontales máx. 6 ítems (más → tabla). Columnas <30 puntos; línea si ≥31. Serie actual en color de marca, comparada en gris. |
| [Sigma — waterfall](https://www.sigmacomputing.com/blog/waterfall-charts-data-visualization) | Barras ancla sólidas en los subtotales, conectores punteados, eje desde cero para usuarios no financieros. |
| [Tableau Data Stories](https://www.tableau.com/blog/tableau-natural-language-data-stories) | Narrativa por **reglas y plantillas**, no LLM. 2–3 frases, siempre con un número, una causa y un link. |

**Galerías para inspiración visual:** [Mobbin](https://mobbin.com) · [SaaSFrame — dashboards](https://www.saasframe.io/categories/dashboard) · [Tremor](https://www.tremor.so/) (componentes React/Tailwind de KPI cards, compatible con tu stack).

---

## 2. Qué datos debe tener el tab

Prioridad por *valor para el dueño de ferretería ÷ esfuerzo*, cruzada con lo que ya tienes en backend.

### Tier 1 — el MVP del rediseño

| # | Métrica | Fórmula | Estado en tu backend |
|---|---|---|---|
| 1 | **Utilidad del periodo** (métrica héroe) | `utilidad_neta` + % sobre ventas | ✅ `/reportes/resultados` |
| 2 | **Ventas netas** | subtotal sin IVA − devoluciones − descuentos | ⚠️ `ingresos` existe; faltan devoluciones y descuentos como línea |
| 3 | **Margen bruto % + COP** | `(ventas netas − COGS) / ventas netas` | ✅ derivable; ⚠️ falta exponerlo como % |
| 4 | **Δ vs. periodo anterior** en toda métrica | — | ❌ **gap**: solo existe en `ResumenGastos.gasto_periodo_anterior` |
| 5 | **Margen por categoría + mix** | margen % por categoría + `ventas_cat / ventas_total` | ✅ `/reportes/margen-productos?por=categoria` (falta la columna de mix) |
| 7 | **Punto de equilibrio del mes** | `gastos fijos mes / margen bruto %` | ✅ ya calculado en `/reportes/gastos` — hay que traerlo aquí |
| 8 | **Flujo de caja del periodo** | entradas − salidas | ✅ `/reportes/flujo-dinero` (muy completo) |

> **Descartado — cartera por edades y DSO (era el #6).** El borrador lo ponía como el cambio de mayor
> impacto, por ser el dolor #1 del ferretero colombiano en general. **No aplica a Punto Rojo:** el dueño
> confirmó (2026-07-31) que casi no fía — del orden de un fiado por semana. No amerita endpoint de
> aging, buckets, provisión ni widget propio; un dato así se lee en un renglón. Si algún día hace falta,
> el lugar es un apartado chico en Historial o en Hoy, **no** una sección de Resultados. Lo que ya está
> resuelto y basta: el fiado no cuenta como entrada de caja y se muestra aparte en el flujo de dinero.
>
> Esto arrastra: se caen también **DSO** (#6), **CCC** (#13, que lo necesita) y la regla de narrativa de
> cartera vencida (§4). Si el negocio cambia y el fiado crece, el patrón a copiar está a la mano:
> `/reportes/aging-cxp` → `AgingProveedor` (buckets, `mas_vieja_dias`, semáforo), `fiados.creado_en` y
> `fiados.saldo` ya existen —no haría falta migración—, y las provisiones de referencia son las de
> [Alegra](https://blog.alegra.com/colombia/cartera-por-edades/): 1–30 → 0%, 31–60 → 5%, 61–90 → 5%,
> 91–180 → 10%, +180 → 15%.

### Tier 2 — segunda iteración

| # | Métrica | Fórmula | Estado |
|---|---|---|---|
| 9 | **Ticket promedio** | `ventas netas / n° transacciones` | ✅ `/reportes/resumen` (día); falta por rango |
| 10 | **Rotación de inventario + DIO** | `COGS / inventario promedio al costo`; `DIO = 360 / rotación` | ⚠️ hay `productos.costo_promedio` y el balance del ledger; **falta snapshot mensual de inventario valorizado** |
| 11 | **GMROI** | `margen bruto $ / inventario promedio al costo` = `rotación × margen %` | ❌ depende de #10 |
| 12 | **DPO** | `CxP promedio / compras del periodo × 360` | ⚠️ `/reportes/aging-cxp` da el saldo; falta el flujo de compras |
| 13 | ~~**CCC**~~ | `DIO + DSO − DPO` | ❌ descartado: sin DSO no hay CCC (ver el recuadro de Tier 1) |
| 14 | **Descuentos otorgados** (COP y % de ventas) | — | ❌ **gap**: es donde se fuga el margen en mostrador |
| 15 | **Inventario muerto valorizado** | Σ(unidades × costo) de SKUs sin venta en 180d | ❌ gap (datos existen en kardex) |
| 16 | **Puente utilidad → caja** | utilidad − Δcartera − Δinventario + ΔCxP − retiros | ⚠️ `/contabilidad/flujo-efectivo` puede darlo si el ledger está sembrado |

### Tier 3 — nice-to-have

Serie de utilidad/margen en el tiempo (hoy solo hay serie de ventas), merma/faltantes, ventas por empleado, crecimiento YoY, concentración de clientes (% del top 10).

### Benchmarks para contextualizar (usar con cuidado)

| Indicador | Ferretería Colombia | Ferretería EE.UU. (NHPA/NRHA) |
|---|---|---|
| Margen bruto | **20–35%** ([Treinta](https://www.treinta.co/blog/vale-la-pena-abrir-una-ferreteria-en-colombia-en-2025-rentabilidad-consejos-y-herramientas-clave)) | 42,6% ([CODB 2025](https://hardwareretailing.com/staying-on-par-highlights-from-the-2025-cost-of-doing-business-study/)) |
| Rotación de inventario | 4–8x (DIO 45–90 días) | 4–6x |
| GMROI | — | **134,1%** típica / **198,1%** top 25% ([NHPA](https://yournhpa.org/wp-content/uploads/2018/04/2016-CODB-GMROI.pdf)) |
| Utilidad antes de impuestos | 8–12% (sin descontar sueldo del dueño) | 4,7% |

⚠️ **No presentes el benchmark gringo como meta.** 42,6% vs. 20–35% real colombiano destruye la credibilidad del producto. Si los muestras, etiquétalos como referencia internacional.

**Márgenes típicos por categoría** (útiles como referencia en la tabla de margen): tornillería 18–25% · herramienta eléctrica 20–30% · pinturas 25–35% · EPP 22–32% · marca propia 30–45% · herramienta manual 25–35%. Patrón universal: **cemento y varilla son tráfico con margen bajo; tornillería, pintura y marca propia son el margen.**

---

## 3. Estructura propuesta del tab

```
╔═══════════════════════════════════════════════════════════════════════╗
║ Resultados financieros              [Julio 2026 ▾] [vs. Junio ▾] [⭳] ║
║                                     ⓘ Periodo en curso · datos al 29  ║
╠═══════════════════════════════════════════════════════════════════════╣
║  UTILIDAD DEL PERIODO                                                 ║
║  $14.500.000       ▲ +20,8% vs. junio        15,8% de las ventas     ║
║  ╱╲__╱╲___╱─  (sparkline 12 meses)                                    ║
║                                                                       ║
║  ┌─────────────────────────────────────────────────────────────────┐ ║
║  │ Vendiste $92,0M (+8,2%), pero la utilidad solo creció 1,3%:     │ ║
║  │ el costo de mercancía subió del 64,0% al 66,3% de las ventas.   │ ║
║  │ Mayor impacto: Plomería.                            Ver detalle →│ ║
║  └─────────────────────────────────────────────────────────────────┘ ║
╠═══════════════════════════════════════════════════════════════════════╣
║ ┌──────────┐┌──────────┐┌──────────┐┌──────────┐                     ║
║ │Ventas    ││Costo     ││Utilidad  ││Ticket    │                     ║
║ │netas     ││mercancía ││bruta     ││promedio  │                     ║
║ │$92,0M    ││$61,0M    ││$31,0M    ││$127.400  │                     ║
║ │▲+8,2%    ││66,3% vts ││33,7% vts ││▼−2,1%    │                     ║
║ │╱╲_╱      ││▲+12,1% ⚠ ││▲+1,3%    ││╲╱╲_      │                     ║
║ └──────────┘└──────────┘└──────────┘└──────────┘                     ║
║ ⓘ Margen sobre el 78% de las ventas · 22% sin costo → Completar      ║
╠═══════════════════════════════════════════════════════════════════════╣
║ DE LA VENTA A LA UTILIDAD (waterfall)  │ PUNTO DE EQUILIBRIO         ║
║                                        │ ▓▓▓▓▓▓▓▓░░░ 63%             ║
║ Ventas brutas ▉▉▉▉▉▉▉▉▉▉               │ $26,8M de $42,3M            ║
║ − Descuentos      ▉                    │ Día 18 de 30 · vas bien     ║
║ − Devoluciones    ▉                    ├─────────────────────────────╢
║ = Ventas netas ▉▉▉▉▉▉▉▉▉               │ FLUJO DE CAJA               ║
║ − Costo merc.  ▉▉▉▉▉▉                  │ Entró $88M · Salió $79M     ║
║ = U. bruta     ▉▉▉                     │ Neto +$9M                   ║
║ − Gastos           ▉▉                  │ [ver desglose →]            ║
║ = UTILIDAD     ▉▉                      │                             ║
╠═══════════════════════════════════════════════════════════════════════╣
║ MARGEN POR CATEGORÍA                                                  ║
║ Pintura     ▉▉▉▉▉▉ 31,2%   Tornillería ▉▉▉▉▉ 24,8%   Cemento ▉▉ 11,4% ║
╠═══════════════════════════════════════════════════════════════════════╣
║ ESTADO DE RESULTADOS          [Expandir todo] [Excel ⭳] [Contador ⭳] ║
║ Concepto        │ Julio     │ % vtas │ Junio    │  Δ$    │  Δ%       ║
║ ▸ Ingresos      │ $92,0M    │ 100,0% │ $85,0M   │ +7,0M  │ +8,2%     ║
║ ▾ Costo ventas  │ ($61,0M)  │  66,3% │ ($54,4M) │ −6,6M  │ +12,1%    ║
║    Herramienta  │ ($18,0M)  │  19,6% │ ($16,0M) │ −2,0M  │ +12,5%    ║
║ ▸ Utilidad bruta│ $31,0M    │  33,7% │ $30,6M   │ +0,4M  │ +1,3%     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### Decisiones de la propuesta

**Sub-tabs → una sola página.** Los tres sub-tabs actuales fragmentan una historia que es una sola: ganaste esto (P&L), la plata se movió así (flujo), y viene de aquí (margen por categoría). IBCS lo llama explícitamente un defecto ("baja densidad de información: consolida el contexto en una página en vez de distribuirlo en pantallas"). Si el scroll queda muy largo, colapsa las secciones de detalle, no las escondas detrás de tabs.

**El waterfall reemplaza el bar chart.** `recharts@2.12.7` ya está en el proyecto — se implementa con un `BarChart` de dos series, una transparente (offset) y una visible. [Ejemplo oficial](https://recharts.github.io/en-US/examples/Waterfall/). Reglas: barras ancla sólidas en los subtotales (Ventas netas, U. bruta, Utilidad), conectores punteados, **etiqueta doble: valor absoluto Y % de ventas netas**, eje desde cero, click en barra → drill-down.

**Presets de periodo, no dos inputs de fecha.** Hoy son dos `<input type="date">`. Deben ser: Hoy · Esta semana · Este mes · Mes pasado · Trimestre · Año corrido · Personalizado. Los presets cubren el 90% de los casos. `mesActualCO()` sigue siendo el default.

**Comparador como control separado.** `Comparar con: [Periodo anterior ▾ | Mismo periodo año pasado | Sin comparación]`. Todo Δ% en pantalla se recalcula contra lo elegido y **el label nombra contra qué compara** — "▲ +1,3%" solo es ruido; "▲ +1,3% vs. junio" es información.

**Periodo en curso marcado.** Julio 2026 al día 29 no es comparable con junio completo. Sombreado gris + nota "datos al 29 de julio" (patrón Toast).

**Semántica de dirección por métrica.** En "Costo de mercancía", "Descuentos" y "Gastos", ▲ es **malo**. No pintes todo aumento de verde. Necesitas un prop `direccion: 'mas_es_mejor' | 'menos_es_mejor'` en el componente `Metric`.

**Toda cifra es un link.** Regla de Shopify. Y el drill-down hereda el periodo y el comparador activos (regla de Lightspeed). Máximo 3 saltos hasta el documento (regla de Loggro). Preferir panel lateral (`sheet.jsx`, ya está en el proyecto) sobre navegación completa en el primer salto.

**Formato colombiano estricto.** Punto como separador de miles, `$` antes, negativos entre paréntesis contables `($1.200.000)`. Abreviaturas (`$92,0M`) solo en ejes de gráfico — **nunca en KPI cards ni en la tabla del estado de resultados**.

---

## 4. Narrativa automática — empezar por reglas, no por LLM

La tarjeta de resumen es lo que convierte el tab de "tabla de números" a "me dice qué hacer". Tableau lo hace con NLG basada en plantillas, no con modelo generativo, y su justificación aplica exacto a tu usuario: reducir la barrera para quien no interpreta gráficos con soltura.

Cuatro reglas cubren casi todo:

```js
// Δ margen > 2pp
"Tu margen {subió|bajó} {X} puntos. La causa principal fue {categoría de mayor contribución}."
// Descuentos > umbral
"Los descuentos fueron el {X}% de las ventas, arriba del {Y}% habitual."
// Gasto con Δ% > 20% y monto material
"{Gasto} creció {X}% este mes ({COP})."
// Progreso a punto de equilibrio
"Día {D} de {T}. Llevas {COP} de los {COP} que necesitas para no perder. Al ritmo actual cierras en {COP}."
```

Máximo 2–3 frases, **siempre con un número, una causa y un link**. Nunca "las ventas tuvieron un desempeño variable".

Cuando quieras subir de nivel, el insight de mayor valor es la **descomposición precio-volumen-mix** ([metodología FTI](https://www.fticonsulting.com/insights/white-papers/quantifiable-approach-price-volume-mix-analysis)):

> *"Tu margen bajó de 31% a 28% (−3pp). De esos: −2,1pp por mix (vendiste 40% más cemento, que deja 12%, y 15% menos pintura, que deja 30%); −1,4pp porque el proveedor te subió el hierro y no ajustaste precio; +0,5pp porque subiste precios de tornillería."*

---

## 5. Resolver la duplicación con `TabEstadosFinancieros`

Tienes dos estados de resultados que van a dar cifras distintas:

| | `/resultados` (TabResultados) | `/estados-financieros` (ledger, ADR 0030) |
|---|---|---|
| Fuente | Agregación directa de ventas/gastos | Asientos de doble partida |
| Base | Caja/operativa | Causación formal |
| Gate | admin | admin + `contabilidad_ledger` |
| Audiencia | El dueño | El contador |

**Recomendación:** posiciónalos explícitamente como *gerencial* vs. *contable*, con esos nombres en la UI, y pon en el tab de Resultados una nota de una línea: *"Vista gerencial. Para los estados formales que le entregas a tu contador, ve a Estados financieros."* Y a la inversa. Si algún día divergen mucho, agrega un conciliador; por ahora basta con que el usuario sepa cuál está mirando y por qué.

Corolario relacionado: el **toggle "Ver por causación / Ver por caja"** que recomiendan los buenos productos ya lo tienes resuelto de facto — el P&L es causación y el Flujo de dinero es caja. Solo falta **etiquetarlo explícitamente en la UI** y, si se puede, el puente entre ambos:

```
Utilidad del mes (causación)               $12.000.000
  − Aumento de cartera (fiaste más)        ($5.400.000)
  − Aumento de inventario (compraste más)  ($7.200.000)
  + Aumento de deuda a proveedor            $3.100.000
  − Retiros del dueño                      ($1.500.000)
= Variación de caja del mes                  ($800.000)
```

> *"Ganaste $12M pero tu caja bajó $800 mil. $5,4M se fueron a fiado y $7,2M a mercancía en bodega. Tu utilidad está en el estante y en la libreta, no en el cajón."*

Ese párrafo es, probablemente, lo más valioso que le puedes mostrar a un ferretero.

---

## 6. Trabajo de backend que habilita todo esto

Ordenado por impacto:

1. **`EstadoResultados` con periodo comparado.** Agregar campos `*_anterior` (o un objeto `comparado`) al schema, igual que ya hace `ResumenGastos.gasto_periodo_anterior`. Sin esto no hay ni un solo Δ% en la pantalla. — *bajo esfuerzo, desbloquea todo*
2. **Descuentos y devoluciones como líneas del `EstadoResultados`.** Hoy `ingresos` ya viene neto; hay que exponer las líneas para que el waterfall pueda dibujarlas.
3. **Traer punto de equilibrio y `margen_bruto_pct` a `/reportes/resultados`** (o consumir `/reportes/gastos` desde este tab). Ya están calculados.
4. **Ticket promedio y n° de transacciones por rango** (hoy `/reportes/resumen` es solo del día).
5. **Snapshot mensual de inventario valorizado al costo.** Es el prerrequisito de rotación, DIO y GMROI — las tres métricas que más diferencian el producto en retail. Job mensual que guarde `Σ(stock × costo_promedio)` por corte.
6. **Serie de utilidad/margen en el tiempo** (hoy solo hay `/reportes/serie-ventas`), para los sparklines de las KPI cards.
7. **Motor de insights por reglas**, en backend, devolviendo `[{tipo, severidad, texto, link, valor_impacto}]` ordenado por impacto en COP.

---

## 7. Trampas de cálculo a vigilar

Las que aplican directo a tu código:

| # | Trampa | Estado en Punto Rojo |
|---|---|---|
| T-1 | **Margen vs. recargo (markup).** Recargar 30% da margen de 23,1%. `Margen = Markup / (1+Markup)` | Vigilar: el dueño va a leer uno como el otro. Etiquetar explícito. |
| T-2 | **IVA dentro del margen.** Los precios de mostrador colombianos suelen ser IVA incluido. | ✅ Ya resuelto: `ingresos` es subtotal sin IVA. Documentarlo en la UI con un tooltip. |
| T-3 | **GMROI: ratio (1,34x) vs. porcentaje (134,1%).** NRHA usa %, Shopify usa ratio. | Elegir una convención antes de implementar y no mezclarla nunca. |
| T-5 | **Unidades de medida inconsistentes** (bulto/metro/kilo/unidad). Si el factor de conversión está mal, **todos los márgenes se corrompen en cascada.** | Riesgo alto en ferretería. Es la fuente #1 de datos basura en POS del sector. |
| T-6 | **DPO sobre ventas en vez de compras.** La fórmula que circula en Colombia (`CxP × 360 / ventas`) subestima el DPO en proporción al margen. | Usar `CxP promedio / compras del periodo × 360`. |
| T-7 | **360 vs. 365 días.** Colombia usa 360; los benchmarks internacionales 365. | Elegir 360 (convención local) y ser consistente al comparar. |
| T-8 | **Saldo final vs. promedio** en denominadores de rotación y cartera. | Usar promedio del periodo, no el corte. Distorsiona negocios estacionales. |
| T-9 | **Retenciones tratadas como gasto.** ReteFuente/ReteIVA/ReteICA practicadas al negocio son **anticipos de impuesto (activo)**, no gasto. | Verificar en `modules/retenciones`. |
| T-10 | **Retiros del dueño como gasto operativo.** | ✅ Ya resuelto (ADR 0071 separa `total_retiro`). |
| T-11 | **Costo congelado vs. costo actual.** El COGS debe usar el costo vigente al momento de la venta. | ✅ Ya resuelto (costo snapshot por venta). Es un acierto grande. |
| T-14 | **GMROI no es rentabilidad** — ignora arriendo y nómina. | No presentarlo como "ganancia". |
| T-15 | **Promediar promedios.** El margen global no es el promedio de los márgenes por categoría; hay que ponderar por ventas. | Vigilar al construir la tabla de categorías. |
| T-16 | **Estacionalidad.** Diciembre vs. enero, o 22 días hábiles vs. 19. | Por eso el comparador debe ofrecer "mismo periodo año pasado". |

---

## 8. Orden de implementación sugerido

**Fase 1 — la base (sin backend nuevo salvo el punto 1)**
1. Comparativo periodo anterior en `EstadoResultados` (backend)
2. Métrica héroe + 4 KPI cards con Δ%, % de ventas, sparkline y dirección semántica
3. Waterfall reemplazando el bar chart
4. Presets de periodo + comparador
5. Fusionar los 3 sub-tabs en una página

**Fase 2 — el diferencial colombiano**
6. Punto de equilibrio traído al tab (barra de progreso)
7. Tabla de estado de resultados jerárquica con columnas `% ventas`, `Δ$`, `Δ%`
8. Narrativa automática por reglas (empezar con 3 reglas)

**Fase 3 — retail avanzado**
9. Snapshot de inventario valorizado → rotación, DIO, GMROI por categoría
10. Inventario muerto valorizado con costo de oportunidad
11. Puente utilidad → caja
12. Descomposición precio-volumen-mix

Si hay que recortar, **Fase 1 + el punto de equilibrio ya dejan el tab mejor que el de cualquier POS colombiano del mercado.**

---

## Fuentes

**Productos de referencia**
- Lightspeed Retail Dashboard — https://x-series-support.lightspeedhq.com/hc/en-us/articles/25533720653211-Using-the-retail-dashboard
- Toast Reporting Dashboard — https://support.toasttab.com/en/article/How-to-Use-the-Toast-Reporting-Dashboard
- Shopify Finance Summary — https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/report-types/default-reports/finances-report
- Xero Business Snapshot — https://www.xero.com/us/accounting-software/analytics/snapshot/
- QuickBooks Dashboards — https://quickbooks.intuit.com/learn-support/en-us/help-article/business-reports/create-dashboards-view-key-business-insights/L7C4Ftrzx_US_en_US
- Square reports — https://squareup.com/help/us/en/article/5072-summaries-and-reports-from-the-online-dashboard
- Bsale reportes — https://ayuda.bsale.io/support/solutions/articles/151000213248-reportes-de-ventas
- Alegra reportes inteligentes — https://ayuda.alegra.com/int/explicaci%C3%B3n-de-los-reportes-inteligentes
- Alegra POS ferreterías — https://www.alegra.com/colombia/pos/ferreterias/
- Siigo análisis vertical/horizontal — https://siigopyme.portaldeclientes.siigo.com/basedeconocimiento/informes-financieros-analisis-financiero/
- Loggro drill-down contable — https://loggro.com/software-contable/software-para-reportes-contables-y-financieros/

**Patrones de UI**
- Sigma — waterfall charts — https://www.sigmacomputing.com/blog/waterfall-charts-data-visualization
- Recharts waterfall — https://recharts.github.io/en-US/examples/Waterfall/
- Cloudscape — tablas con filas expandibles — https://cloudscape.design/patterns/resource-management/view/table-with-expandable-rows/
- Polaris data visualizations — https://polaris-react.shopify.com/design/data-visualizations
- Tableau Data Stories (narrativa) — https://www.tableau.com/blog/tableau-natural-language-data-stories
- Patrones de filtro de fecha — https://evolvingweb.com/blog/most-popular-date-filter-ui-patterns-and-how-decide-each-one

**Principios de diseño**
- IBCS Top/Bottom 5 — https://www.ibcs.com/resource/top-and-bottom-5-of-international-business-communication-standards/
- ClearPoint — KPI dashboard best practices — https://www.clearpointstrategy.com/blog/kpi-dashboard-best-practices
- IGC — dashboard layout — https://www.intelligentgraphicandcode.com/design/dashboard-design/dashboard-layout
- Fintech dashboard design guide — https://www.themasterly.com/blog/fintech-dashboard-design-guide
- Smashing — decluttering data viz — https://www.smashingmagazine.com/2021/11/dashboard-design-research-decluttering-data-viz/
- Tableau — rojo/verde y daltonismo — https://www.tableau.com/blog/examining-data-viz-rules-dont-use-red-green-together
- Paletas accesibles — https://davidmathlogic.com/colorblind/

**Métricas y benchmarks**
- NHPA GMROI (ferreterías) — https://yournhpa.org/wp-content/uploads/2018/04/2016-CODB-GMROI.pdf
- NHPA Cost of Doing Business 2025 — https://hardwareretailing.com/staying-on-par-highlights-from-the-2025-cost-of-doing-business-study/
- Treinta — rentabilidad ferretería Colombia — https://www.treinta.co/blog/vale-la-pena-abrir-una-ferreteria-en-colombia-en-2025-rentabilidad-consejos-y-herramientas-clave
- Márgenes por categoría — https://modelosdeplandenegocios.com/blogs/news/rentabilidad-ferreteria
- GMROI = rotación × margen — https://beancount.io/es/blog/2026/07/13/gmroi-gross-margin-return-on-inventory-investment-guide
- Cartera por edades y provisiones (CO) — https://blog.alegra.com/colombia/cartera-por-edades/ *(descartado para Punto Rojo; queda por si el fiado crece)*
- Punto de equilibrio (CO) — https://blog.alegra.com/colombia/punto-de-equilibrio-paso-a-paso-para-su-calculo/
- Indicadores de actividad (fórmulas CO, 360 días) — https://actualicese.com/indicadores-de-actividad-conoce-como-se-utilizan-mediante-un-caso-practico/
- Inventario muerto / SLOB — https://www.incorta.com/blog/what-is-slob-inventory
- Análisis precio-volumen-mix — https://www.fticonsulting.com/insights/white-papers/quantifiable-approach-price-volume-mix-analysis
- Estados financieros microempresas Grupo 3 (CO) — https://siemprealdia.co/colombia/contabilidad/estados-financieros-para-microempresas/
