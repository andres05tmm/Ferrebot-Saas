# Nota de diseño — Capa de "Misión 1:1" proactiva

> Borrador de diseño (no implementación). Surge del benchmark de Yalo (agente Oris): pasar el agente de **reactivo** a **proactivo**. Corresponde a la idea #6 (reposición proactiva) y #3 (cierre del día) del plan. Candidato a convertirse en ADR.

## Idea en una línea

Un lazo en segundo plano que **detecta oportunidades con reglas deterministas** (sobre el dato que ya existe por tenant), genera "misiones" por cliente, y solo usa el LLM para **redactar** el mensaje. La lógica de negocio nunca vive en el modelo.

## El lazo (8 pasos)

1. **Job programado** — ARQ recurrente, una empresa a la vez, sesión vía `get_tenant_db()`. Reusa la infra de jobs ya prevista para DIAN. Respeta aislamiento de tenant.
2. **Señales (reglas Python, NO IA)** — consultas deterministas sobre tablas existentes:
   - Reposición: cliente que compra X cada ~N días y ya pasó el intervalo (`ventas` + `ventas_detalle`).
   - Fiado por cobrar: saldo en `fiados` con +N días sin abono.
   - Cliente dormido: comprador habitual sin compra en N semanas.
   - Cuenta por pagar a proveedor próxima a vencer / vencida (`facturas_proveedores` con `pendiente > 0`). Único faltante: noción de vencimiento (`fecha_vencimiento` o plazo derivado por proveedor). Alerta interna al dueño — no hay tercero externo.
   - Alerta al dueño: stock bajo, producto sin rotación.
3. **Cola de misiones** — tabla nueva en `migrations/tenant/`: `misiones` (cliente_id, tipo, contexto, prioridad, estado, `dedupe_key UNIQUE`, fecha_objetivo). La `dedupe_key` evita duplicar en re-runs (idempotencia).
4. **Política de envío** — tope de frecuencia por cliente, horario hábil (zona horaria Colombia), opt-out, y **feature flag por empresa** (`misiones_proactivas`).
5. **Redacción (único paso con LLM)** — Haiku convierte "misión + contexto" en mensaje natural; misiones simples usan plantilla (patrón bypass).
6. **Aviso al dueño** — ver decisión clave abajo.
7. **Acción** — la respuesta del dueño re-entra al **pipeline existente** (bypass → `ai/tools.py`): "sí, registra" dispara `registrar_venta` / `abonar_fiado`. No se reimplementa lógica.
8. **Resultado → lazo** — se marca convirtió/no; alimenta observabilidad por empresa (como la tasa de bypass). El dato afina qué señales valen la pena.

## Decisión de diseño clave: v1 dirigida al DUEÑO, no al cliente final

En FerreBot el que está en Telegram es el **ferretero**, no su cliente (a diferencia de Yalo, donde las marcas tienen opt-in de WhatsApp del comprador). Por eso:

- **v1:** las misiones se le muestran al **dueño** con botones de acción ("Pedro suele llevar cemento cada 15 días y ya van 18 — ¿le escribo?"). El dueño aprueba (human-in-the-loop) y actúa.
- **v2 (después):** escribirle directo al cliente, solo cuando haya contacto y consentimiento.

Esto resuelve **Habeas Data (Ley 1581)**: no se contacta a terceros sin consentimiento; el dueño decide.

## Qué es nuevo vs. qué se reusa

- **Reusa:** jobs ARQ, sesión por tenant, `ai/tools.py`, bot de Telegram, SSE, feature flags, idempotencia.
- **Nuevo (pequeño):** tabla `misiones`, módulo de reglas de señales, scheduler, política de frecuencia.

## Primer paso sugerido

Arrancar con **una sola señal** end-to-end (reposición *o* fiado vencido), validar utilidad con el dueño, y luego sumar reglas.

## Pendientes a definir

- ¿Primera señal: reposición o cobro de fiado?
- Parámetros por empresa (intervalos, topes de frecuencia, horario).
- ¿Se documenta como ADR formal antes de construir?
