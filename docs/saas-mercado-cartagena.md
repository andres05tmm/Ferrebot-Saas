# SaaS para comercios de Cartagena — qué construir después del POS

> Documento de estrategia (7 jun 2026). Hoy tenemos un **POS multi-tenant** con bot Telegram y
> emisión DIAN síncrona. Pregunta de Andrés: *¿qué más implementar para vender a negocios de mi
> ciudad?* Esto cruza dos cosas: **(1) qué exige/quiere el mercado local** (investigado en la web) y
> **(2) qué infraestructura SaaS nos falta** para venderlo. No es código todavía: es el mapa para
> decidir el siguiente frente grande.

---

## TL;DR — recomendación

El producto ya hace lo difícil (POS + inventario + caja + DIAN + bot IA, todo multi-tenant). Lo que
**falta para vender en Cartagena** no es más POS: son tres puentes hacia el comercio real.

1. **Cobro electrónico obligatorio bien resuelto (documento equivalente POS electrónico).** Desde el
   **1 de junio de 2025** los pequeños comercios están **obligados** a emitir el POS electrónico ante
   la DIAN; el tiquete de papel ya no soporta costos. Es una **obligación legal vigente** → la cuña de
   venta más fuerte: "te pongo al día con la DIAN sin que cambies tu forma de vender".
2. **Pagos: Bre-B + link/QR.** **Bre-B** (pagos inmediatos del Banco de la República, **gratis, 24/7,
   con QR y "llave" de comercio**) está en despliegue 2025-2026 y va a ser el rail dominante. Integrar
   **cobro por QR Bre-B + conciliación automática en caja** es un diferenciador que casi nadie pequeño
   tiene aún. Complemento: link de pago (Wompi/Bold, que ya traen Nequi/Daviplata/PSE).
3. **Canal WhatsApp, no Telegram.** El **87% de las mipymes colombianas vende por WhatsApp**. Nuestro
   bot vive en Telegram. Portar el agente a **WhatsApp Business API** es el mayor cierre de brecha
   producto-mercado. (Ojo: Meta prohibió en ene-2026 los chatbots de IA *de propósito general*; el
   nuestro es especializado en ventas/pedidos → permitido.)

Y para **vender** todo esto necesitamos rematar la **capa comercial SaaS** (Fase 16): planes, cuotas,
billing y onboarding autoservicio. Sin eso tenemos producto pero no negocio.

**Apuesta sugerida para la próxima ola:** **(A) documento equivalente POS electrónico** (obligación
legal = razón de compra inmediata) **+ (B) Bre-B/cobro** (diferenciador), apoyados en un mínimo de
**billing/planes** para poder cobrar. WhatsApp es la ola 2 (mayor alcance, más integración externa).

---

## Lo que YA tenemos (capa SaaS)

| Bloque | Estado |
|---|---|
| Multi-tenancy (DB por empresa) + control DB | ✅ operando (Punto Rojo = tenant #1) |
| Provisioning de empresa (`tools/provision_tenant`) | ✅ script idempotente |
| **Feature flags por empresa** (catálogo rico, enforcement API/dashboard/bot) | ✅ `feature-flags.md` |
| RBAC (super_admin / admin / vendedor) | ✅ |
| POS núcleo: ventas, inventario, caja, gastos, clientes, proveedores, reportes | ✅ |
| Fiscal DIAN: factura electrónica, documento soporte, notas, libro IVA, compras fiscal | ✅ síncrono |
| Bot Telegram + ventas por voz (Whisper) | ✅ |
| Dashboard web React (white-label, tema por empresa) | ✅ |
| Observabilidad (Sentry cableado), backups + DR, uptime | ✅ (red de seguridad cerrada) |

**Lo que NO tenemos (y bloquea vender):** billing/planes/cuotas, **onboarding autoservicio** (panel
super-admin de alta), **integración de pagos** (hoy el método de pago es solo una etiqueta, no cobra
de verdad), **canal WhatsApp**, **nómina electrónica**, y confirmar que emitimos el **documento
equivalente POS electrónico** específico (no solo la factura de venta).

---

## Lo que el mercado de Cartagena exige / quiere

### 1. Obligación legal: documento equivalente POS electrónico (DIAN)
Calendario ya **vencido** para todos los grupos: grandes 1-feb-2025, medianas 1-abr-2025, **pequeños e
independientes 1-jun-2025**. El tiquete POS de papel ya **no sirve** para soportar costos/gastos; debe
transmitirse a la DIAN. Esto convierte "ponerte legal" en una **necesidad activa, no opcional**, para
cualquier ferretería/tienda/restaurante de Cartagena. Ya tenemos el canal MATIAS y la emisión fiscal:
el trabajo es exponer/operar el POS electrónico como flujo de caja diario, no construir el rail desde cero.

### 2. Pagos: Bre-B es el cambio estructural
**Bre-B** (Banco de la República): transferencias inmediatas **interoperables entre todos los bancos**,
**gratis**, 24/7, liquidación en 6-8 segundos. Para comercios hay **llave de comercio** (código de ~10
caracteres, una por caja/sucursal si se quiere) y **pago por QR**. Registro de llaves abrió 2º semestre
2025; en 2026 se amplían los tipos de llave. Oportunidad: **mostrar QR Bre-B en el cobro → cliente paga →
conciliar el ingreso automáticamente en caja**. Complemento de tarjeta/online: **Wompi** (de Bancolombia,
trae botón Nequi/Bancolombia/PSE y **links de pago**) y **Bold**.

### 3. Canal: WhatsApp manda
**87% de las mipymes** usan WhatsApp como canal principal de ventas; las tiendas que más venden lo usan.
La **API de WhatsApp Business** permite multiagente, automatización y bots de pedidos. Costo típico para
una tienda mediana: **COP 150.000–500.000/mes**; hay tramo gratuito (~1.000 conversaciones/mes). Nuestro
agente IA de ventas encaja en lo que Meta sí permite (bots especializados de negocio).

### 4. Nómina electrónica (segmento con empleados)
Obligatoria para **personas jurídicas** (sin importar ingresos) y naturales > 1.400 UVT. Transmisión en
los **10 días hábiles** tras el cierre de mes. Relevante para comercios con planilla; **no** para el
micro-negocio de una persona. Va como **feature flag opcional**, ola posterior.

### 5. Panorama competitivo (para posicionar precio)
**Alegra**: facturación desde **$17.900/mes**, plan Pyme **$149.900** (POS+nómina+facturación incluidos),
mes a mes sin penalidad, soporte 24/7. **Siigo**: desde **~$146.000/mes**, compromiso anual, cobra módulos
aparte. Lectura: hay que **quedar al nivel o por debajo de Alegra** y diferenciar por lo que ellos **no**
tienen bien: **agente de IA de ventas en WhatsApp + cobro Bre-B + ajuste vertical** (p. ej. ferretería con
fracciones y precio por longitud/peso). No competir por "otro software contable más".

---

## Matriz: necesidad → feature → tenemos / falta

| Necesidad del comercio | Feature en el SaaS | ¿Tenemos base? | Esfuerzo | Valor de venta |
|---|---|---|---|---|
| Estar legal con la DIAN (POS papel ya no sirve) | **Documento equivalente POS electrónico** como flujo diario | Sí (MATIAS + emisión fiscal) | **Bajo-medio** | **Muy alto (obligación)** |
| Cobrar fácil y gratis | **Bre-B**: QR de cobro + conciliación en caja | Parcial (caja existe; falta cobro real) | Medio | **Alto (diferenciador)** |
| Cobrar tarjeta/online | **Link de pago** (Wompi/Bold) + estado de cobro | No | Medio | Medio-alto |
| Vender/atender donde está el cliente | **Canal WhatsApp** (portar el agente) | Sí (bot IA; falta canal) | **Alto** (API Meta, plantillas, opt-in) | **Muy alto (alcance)** |
| Poder cobrarle al comerciante | **Billing + planes + cuotas** | No (Fase 16) | Medio | Habilitador (sin esto no hay negocio) |
| Darse de alta sin nosotros | **Onboarding autoservicio** (panel super-admin) | Parcial (script provisioning) | Medio | Habilitador |
| Cumplir con empleados | **Nómina electrónica** (flag opcional) | No | Alto | Medio (segmento) |

---

## Secuencia recomendada (tres olas)

**Ola 1 — "Vendible y legal" (núcleo del negocio en Cartagena)**
- POS electrónico (documento equivalente) operando como flujo de caja diario + verificación de paridad fiscal.
- Cobro **Bre-B** (llave de comercio + QR) con conciliación automática en caja.
- **Billing/planes mínimo**: definir 2-3 planes (precio ≤ Alegra), medir uso, estado de suscripción.
- Cierra: una empresa nueva de Cartagena puede **darse de alta, pagarnos, cobrar por Bre-B y emitir POS electrónico**.

**Ola 2 — "Donde está el cliente" (alcance)**
- **Canal WhatsApp Business API**: portar el agente de ventas/pedidos; plantillas, opt-in, multiagente.
- **Onboarding autoservicio** pulido (panel super-admin → alta de extremo a extremo).
- Link de pago (Wompi/Bold) como complemento de Bre-B.

**Ola 3 — "Profundidad por vertical/segmento"**
- Nómina electrónica (flag opcional para comercios con planilla).
- Adaptadores por vertical (restaurante: mesas/comandas; farmacia: lotes/vencimientos; ferretería ya cubierta).
- Segunda empresa-cliente real (cierre de M2 del roadmap).

---

## Primeros prompts para Claude Code (Ola 1, si confirmamos)

> Borradores; los afino cuando Andrés elija el hilo de arranque.

**P1 — Auditar emisión POS electrónico**
```
Audita el módulo de facturación (modules/facturacion + services/...): ¿emitimos el "documento
equivalente POS electrónico" de DIAN, o solo la factura electrónica de venta y el documento soporte?
Lista los tipos de documento que MATIAS expone hoy en el código, el endpoint/flujo que los dispara,
y qué faltaría para emitir POS electrónico como cierre de cada venta. Solo lectura: dame un informe,
no cambies código.
```

**P2 — Diseño de cobro Bre-B (ADR)**
```
Necesito un ADR (docs/adr/) para integrar cobro Bre-B en el POS: llave de comercio por tenant
(secreto cifrado en control DB, como MATIAS), generación de QR de cobro por venta, y conciliación del
ingreso en caja vía webhook o consulta de estado. Evalúa: integrar vía un PSP/agregador que ya exponga
Bre-B (Wompi/otros) vs. integración directa. Trade-offs, modelo de datos mínimo, y feature flag
'cobro_breb'. No implementes aún.
```

**P3 — Esqueleto de billing/planes**
```
Diseña (system-design, sin implementar) la capa de billing en el control DB: tablas planes,
suscripciones, medición de uso (ventas/emisiones/vendedores) y estado (activa/morosa/suspendida).
Cómo se cruza con feature flags y con el límite por plan. Propón 3 planes con precio de referencia
≤ Alegra. Entregable: docs/billing.md + ADR de "cobro manual documentado vs PSP" para el mes 1.
```

---

## Fuentes

- DIAN — Documento equivalente POS electrónico (ABC y calendario): https://www.dian.gov.co/impuestos/factura-electronica/Documents/Abece-POS-Electronico-documento-equivalente.pdf · https://micrositios.dian.gov.co/sistema-de-facturacion-electronica/calendario-de-implementacion/
- Calendario POS electrónico (grupos y fechas): https://invoway.com/latam/blog/documento-equivalente-electronico-cronograma-2024-2025/ · https://siemprealdia.co/colombia/impuestos/tiquetes-pos-fisicos-y-documento-equivalente-pos-electronico/
- Bre-B (Banco de la República): https://www.banrep.gov.co/es/bre-b/que-es · https://www.banrep.gov.co/es/bre-b
- Bre-B para comercio (QR, llaves): https://www.retaildelfuturo.com/bre-b-el-nuevo-sistema-de-pagos-inmediatos-que-promete-transformar-el-comercio-en-colombia/
- Pasarelas de pago Colombia (Wompi/Bold/Nequi/Daviplata): https://www.tiendanube.com/blog/pasarelas-de-pago-colombia/ · https://docs.wompi.co/en/docs/colombia/metodos-de-pago/
- WhatsApp Business API comercios Colombia (87% mipymes, costos, regla Meta 2026): https://chatsell.net/whatsapp-business-ecommerce-colombia-automatizar-ventas-2026/ · https://www.macsoft.com.co/whatsapp-business-api-colombia/
- Nómina electrónica DIAN (obligados, plazos): https://www.bbva.com/es/co/empresas/nomina-electronica-plazos-y-requisitos-de-transmision-a-la-dian-para-mipymes/
- Competencia Alegra vs Siigo (precios 2026): https://programascontabilidad.com/comparativas-de-software/precios-de-software-contable-colombia-2026/
