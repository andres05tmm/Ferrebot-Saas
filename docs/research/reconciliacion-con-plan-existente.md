# Reconciliación — `plan-mejoras-2026` vs. lo ya decidido en el proyecto

> **Importante.** El plan y las notas de `docs/research/` se armaron a partir de los docs fundacionales (`architecture.md`, `ai-tools.md`) + investigación de mercado, **sin** tener a la vista los ADRs 0013–0018 ni `plan-impulso-agentes-2026.md`. Al leerlos, gran parte de "nuestras propuestas" **ya está decidida** (a veces distinto a como lo anotamos). Este documento reconcilia: qué ya existe, qué contradijimos, y qué es **genuinamente nuevo**.
>
> **Fuente de verdad:** `plan-impulso-agentes-2026.md` + ADRs 0013–0018 + `feature-flags.md`. Donde haya conflicto, mandan ellos.

## Lo más importante de un vistazo

- El proyecto ya se reposicionó como **"empleados digitales para negocios colombianos"** (agentes de WhatsApp), con lo fiscal/POS como cuña. Nuestra investigación de competidores llegó **por separado a la misma conclusión** → es validación externa, no una propuesta nueva.
- El **patrón de pack** (tablas + motor determinista + herramientas function-calling acotadas + flag + cron ARQ) ya es el estándar. Nuestra "capa de misión proactiva" **es** ese patrón.
- El **doble canal** (Telegram interno / WhatsApp cliente) ya está codificado vía `canal_whatsapp` + packs de servicio + ADR 0018 (familias retail vs. servicios).

## Mapeo idea por idea

| Idea nuestra (#) | Estado real | Referencia |
|---|---|---|
| #1 Cobro de fiados | **YA DECIDIDO** — y por **WhatsApp de cara al deudor** (no Telegram interno). Motor + cron + opt-out + tono fijo + página Cartera | ADR 0015 `pack_cobranza` |
| #11 Pagos embebidos | **YA DECIDIDO** — Bre-B vía **Bold** (PSP), `PagosPort`, webhook idempotente, flag `pagos_online`, infra v1 implementada. NO requiere "revertir" nada | ADR 0013 |
| #7 Cross-sell | **YA** — vive en el agente de cara al cliente | ADR 0017 `pack_ventas` |
| #8 Recuperación de cotizaciones | **YA** — extiende `cotizaciones` | ADR 0017 `pack_ventas` |
| #10 Voz | **YA** — flag `ventas_voz` (Whisper) | `feature-flags.md` |
| #3 Cierre del día / "buenos días" | **YA CONTEMPLADO** — página Hoy/Inicio + analítica del dueño | `plan-impulso` §4, Ola 3 |
| #4 Reportes en lenguaje natural | **PARCIAL** — la página Reportes existe; "en lenguaje natural" sería un delta menor sobre `generar_reporte` | `plan-impulso` §4 |
| Capa de misión proactiva | **YA = patrón de pack** (motor + cron ARQ + tools + flag) | `plan-impulso` §2; pack_agenda/cobranza |
| Doble canal / dos personas | **YA** — `canal_whatsapp` + packs servicio + familias | ADR 0018, `feature-flags.md` |
| #6 Reposición proactiva | **PARCIAL** — al cliente = `pack_ventas`; al dueño (lista sugerida) = idea suelta no cubierta | parcial ADR 0017 |
| #5 Perfil del cliente | **DÉBIL/nuevo** — no explícito en los packs | — |
| #12 Capital, #13 Marketplace | **Estacionados** (coincide: tampoco están en el plan del proyecto) | — |

## Contradicciones que introdujimos (a corregir en las notas)

1. **Canal de cobranza.** En `plan-mejoras` (Fase 1) puse el cobro de fiados como **Telegram interno**. Es **incorrecto**: ADR 0015 lo pone en **WhatsApp de cara al deudor**, con guardarraíles de tono y opt-out. La matriz de `nota-doble-canal` queda corregida por esto.
2. **Pagos.** `nota-doble-canal` y la Fase 3 dicen que los pagos "revierten la decisión de quitar Wompi/Bold y requieren ADR". Ya hay ADR (0013) y la decisión es **Bre-B vía Bold**, sin volverse agregador. Nuestra versión está desactualizada.

## Lo genuinamente NUEVO (no cubierto por ADRs ni `plan-impulso`)

Estos sí son aportes reales y candidatos a ADR **cuando se prioricen** (el proyecto escribe el ADR al momento de construir, regla de oro de `plan-impulso` §3):

- **D1 — Cuentas por pagar a proveedores (#22).** Espejo de `pack_cobranza` pero para lo que el negocio **debe** a sus proveedores (alerta interna al dueño). Reusa `facturas_proveedores`/`facturas_abonos` (ya existen). Falta noción de vencimiento. Candidato a `pack_pagar` o a analítica del dueño. **Junto con cobranza = foto completa de caja.**
- **D2 — Captura de factura de compra por QR → MATIAS `import-track-id` → XML oficial (#2).** Escanear el QR de la factura del proveedor (trae el CUFE) y traer el documento oficial vía RADIAN, en vez de OCR. Verificado en la doc de MATIAS. Más confiable y deja la compra radicada para acuse.
- **D3 — Score de riesgo de fiado (#9).** Analítica sobre `fiados_movimientos` para decidir a quién conviene fiar. Decisión-soporte interna; no existe en el proyecto.

## El aporte que NO se pisa con nada

- **`benchmarking-competidores.md`** — investigación de mercado externa (Yalo, Toast, Square, Alegra/Siigo, etc.). Es el entregable net-new de este trabajo y respalda el reposicionamiento que el proyecto ya eligió.
- **Verificación de MATIAS** (RADIAN recepción + eventos, Webhooks `document.accepted/rejected`, Documento Soporte, captura por QR) — conocimiento concreto y accionable para la parte fiscal de compras.

## Recomendación

1. **No crear ADRs que dupliquen.** Cobranza, pagos, cotizaciones, voz, doble canal y la capa proactiva ya están en ADRs o en `plan-impulso`.
2. **Tratar `plan-mejoras-2026.md` como complemento**, no como plan paralelo: encabezado que apunta a `plan-impulso-agentes-2026.md` como fuente de verdad, y dejar de él solo los **3 deltas** (D1, D2, D3) + la investigación.
3. **ADR solo al construir** uno de los deltas. El más limpio y de mejor encaje (espejo de `pack_cobranza`) es **D1 `pack_pagar`**.
