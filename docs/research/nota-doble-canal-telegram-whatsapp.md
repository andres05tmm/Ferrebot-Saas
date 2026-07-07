# Nota de arquitectura — Doble canal: dos personas del agente

> ⚠️ **Corrección (ver `reconciliacion-con-plan-existente.md`).** Esta nota se escribió sin ver ADR 0018 ni `feature-flags.md`: el doble canal **ya está codificado** (`canal_whatsapp` + packs de servicio + familias retail/servicios). Además, la matriz de abajo ponía el **cobro de fiados como Telegram interno** — es **incorrecto**: ADR 0015 (`pack_cobranza`) lo pone en **WhatsApp de cara al deudor**. Tomar esta nota como contexto histórico, no como decisión vigente.
>
> El agente de FerreBot opera en **dos canales con dos audiencias distintas**, no es el mismo bot replicado.

## El split

- **Telegram → IA interna (operación).** Para los **vendedores / administradores** del negocio. Usuarios de confianza. Herramientas operativas completas: `registrar_venta`, caja, inventario, fiados, compras, facturación. Es lo que existe hoy.
- **WhatsApp → IA externa (cliente final).** El negocio contrata una IA para **sus propios clientes**: atención, consulta de precios/disponibilidad, cotización, toma de pedido. Usuario **no confiable**.

## Por qué el split es bueno en costo

- El tráfico de **alta frecuencia** (operación diaria del mostrador) vive en **Telegram = gratis por mensaje**.
- Solo se pagan las **tarifas por conversación de WhatsApp** en lo que mira al cliente (menor frecuencia, y facturable al negocio).
- Mantiene la ventaja de costo donde más pesa (vs. el anti-patrón Zenvia de competir en mensajería).

## Implicaciones de arquitectura (no es "el mismo bot en otro canal")

1. **Dos personas, dos catálogos de herramientas.** El agente de clientes (WhatsApp) **NO** expone nada que mueva dinero/stock directo (`registrar_venta`, `caja`, etc.). Su set es lectura + captura: consultar producto/precio/disponibilidad, cotizar, y **tomar pedido que enruta al dueño para confirmar**. Es otra fachada de los mismos servicios, con RBAC y feature flags distintos.
2. **Guardrails más fuertes en el canal externo:** prompt-injection, no filtrar datos de otros clientes, no inventar precios. Es la pieza "guardrail" del diagrama de arquitectura.
3. **Se conecta con la capa proactiva:** la "v2" de `nota-capa-mision-proactiva.md` (escribirle directo al cliente) **es** este canal de WhatsApp.

## El canal divide las features (principio de producto)

A quién ayuda la feature determina su canal. No es el mismo bot en dos lados; son dos catálogos distintos.

| Telegram — operador (manejar el negocio) | WhatsApp — cliente final (venderle al cliente) |
|---|---|
| Reportes en lenguaje natural | Cross-sell / upsell ("para esa lámina, ¿tornillos?") |
| Cobro de fiados | Recuperación de cotizaciones no cerradas |
| Cuentas por pagar a proveedores | Reposición directa al cliente (v2 de misiones) |
| Cierre del día | Atención / consulta de precio y disponibilidad |
| Voz (nota de voz → pedido) | Toma de pedido que enruta al dueño |
| Reposición: **aviso al dueño** (v1) | |
| Score de riesgo de fiado | |
| OCR de factura de compra | |

Regla práctica: si la feature **sugiere o vende productos**, es de cliente final (WhatsApp) — el operador ya conoce su catálogo. Si la feature **ayuda a administrar** (caja, cartera, inventario, reportes), es interna (Telegram).

## Acceso a WhatsApp: NO usar la plataforma de un BSP de marketing

- Se corre **el propio agente** (bypass + function calling) sobre WhatsApp, igual que en Telegram. No se necesita el flow-builder ni el motor de bot de Treble u otro BSP de marketing.
- Solo se necesita el **"tubo" de WhatsApp**:
  - **Meta Cloud API (directo):** se pagan solo las tarifas de Meta, sin sobreprecio de intermediario. Opción natural cuando tienes tu propio agente.
  - **BSP** (Gupshup, Wati, 360dialog, Treble): acceso + herramientas, pero con costo por línea/agente encima.
- **Punto crítico multi-tenant:** cada negocio necesita **su propia línea de WhatsApp**. El fee por línea de un BSP (~US$100/línea/mes según datos públicos de Treble) **× N tenants** se vuelve carísimo. Meta Cloud API directo abarata mucho el costo por tenant. A escala, la diferencia es enorme.

## Conclusión sobre Treble

De Treble se toma **inspiración de producto** (pulido, flow-builder para campañas — ver `nota-treble-flow-builder.md`), **no** su API ni su plataforma como BSP. Para el canal de WhatsApp, evaluar **Meta Cloud API directo** primero.
