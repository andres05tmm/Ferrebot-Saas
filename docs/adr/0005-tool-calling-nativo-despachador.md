# ADR 0005 — Tool-calling nativo y despachador único (capa IA, Fase 4)

- Estado: Aceptada
- Fecha: 2026-06
- Extiende: ADR 0003 (IA híbrida: bypass + function calling)

## Contexto
El ADR 0003 fijó el híbrido bypass + function calling, pero dejó abierto *cómo* se porta la capa IA de FerreBot. Hoy FerreBot usa tool-calling nativo solo para las 4 mutaciones de plata + `crear_cliente`, y **todo lo demás** pasa por tags de texto (`[VENTA]{…}[/VENTA]`) que un ejecutor de ~700 líneas y **19 tags** interpreta, con un **puente** `tool_uses_a_tags()` detrás del flag `IA_TOOL_CALLING`. El propio código admite que ese puente es frágil. Fase 4 debe decidir qué portar y cómo estructurar el runtime del bot, respetando los constraints duros del repo (secretos cifrados, config por empresa, aislamiento de tenant).

## Decisión

**(a) Tool-calling nativo para todo el flujo del bot.** Se descarta portar el puente `tool_uses_a_tags()` y el ejecutor de 19 tags. Cada operación que hoy es un tag se modela como una herramienta nativa (`ai/tools.py`) con su contrato (envelope de `ai-tools.md` §3). Se conservan las **reglas** de cada tag (cuándo aplica, qué valida, qué calcula el backend), **no su formato de texto**. El modelo solo decide qué herramienta llamar con qué `args`; nunca toca la base.

**(b) Híbrido Claude + OpenAI, agnóstico de proveedor; modelo según `performance.md`.** La capa de herramientas no depende del proveedor: cada uno recibe el mismo catálogo traducido a su formato. Selección de modelo en runtime del bot: **Haiku** para clasificación/operación frecuente y agentes worker; **Sonnet** cuando el turno exige razonar varios pasos o desambiguar/orquestar; **Opus nunca** en runtime del bot (solo diseño/arquitectura fuera de línea). El proveedor/modelo por turno es configurable por empresa, con default de plataforma.

**(c) Un despachador único, agnóstico, dueño de los rieles de validación.** Existe **un solo** despachador (`ai/dispatcher`) que: resuelve empresa (`get_tenant_db()`), inyecta el contexto del envelope, verifica **RBAC** y **capacidades** (`require_feature`; herramienta no habilitada → ni se expone al modelo), aplica **idempotencia** y, **antes de ejecutar la herramienta**, corre los **rieles de validación de voz**:
  1. **Producto desconocido** → no registra, pregunta (evita inventar productos).
  2. **Precio dudoso**: sin `precio_declarado` y `total` del modelo difiere del catálogo > **1 % (mín. 1 peso)** → no registra, pregunta (evita alucinación de precios).
  3. **Confirmación hablada** de gasto/fiado/abono antes de ejecutar.
  El bypass (Python puro) y el tool-calling terminan llamando **al mismo servicio de dominio** — sin reimplementar reglas.

**Constraints no negociables (heredados):**
- **Claves de API** (OpenAI/Claude) **siempre** por la capa de secretos cifrada en el control DB (`SECRETS_MASTER_KEY`); **jamás** hardcode ni en git. Ver `security.md` / `secrets.md`.
- **Umbrales de monto/confirmación** (bypass, confirmación de plata) viven en **`config_empresa` por tenant**, no en código.

## Alternativas descartadas
- **Portar el puente de tags tal cual:** replica la fragilidad que el propio FerreBot reconoce; doble formato (tool_use ↔ tags) que mantener. Rechazada.
- **Dos despachadores (uno por proveedor):** duplica RBAC/rieles/idempotencia y diverge. Rechazada: el despachador es uno y la diferencia de proveedor es solo traducción de formato.

## Consecuencias
- (+) Un solo camino de ejecución (despachador) con RBAC, capacidades, idempotencia y rieles centralizados; menos superficie frágil que los 19 tags.
- (+) Cambiar de proveedor/modelo es configuración, no reescritura; costo/latencia se ajustan por turno (Haiku barato por defecto).
- (+) Secretos y umbrales fuera del código → multi-empresa y rotación de claves sin redeploy.
- (−) Reescribir como herramientas lo que hoy son tags es trabajo inicial mayor que portar el puente.
- (−) Hay que cubrir con pruebas los dos caminos (bypass y tool-calling) que convergen en el mismo servicio.
- A revisitar: catálogo exacto de herramientas que reemplazan los 19 tags (se detalla en `ai-tools.md` §5) y la política de cuándo Haiku vs Sonnet por tipo de turno.
