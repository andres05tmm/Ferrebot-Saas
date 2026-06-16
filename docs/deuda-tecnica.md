# Deuda técnica

> Registro de fast-follows y atajos conscientes. NO bloquean merge; se priorizan aparte.
> Usar `engineering:tech-debt` para evaluar/priorizar.

## Fase 0 (endurecimiento del runtime del agente)

### FF-1 — Conflicto de payload §4 en las otras 5 operaciones idempotentes
- **Qué:** `registrar_venta`, `registrar_gasto`, `registrar_fiado`, `abonar_fiado`, `emitir_factura`
  hoy implementan solo la mitad "replay" de ai-tools.md §4 (misma `idempotency_key` → devuelven el
  resultado original). NO detectan **misma key con payload distinto → `idempotencia_conflicto`**.
  `compras` (Fase 0) es la implementación de referencia del contrato completo.
- **Riesgo:** bajo. El índice UNIQUE evita duplicados; el hueco es que una key reusada con otro
  payload devuelve el original en vez de un 409. No hay corrupción de datos.
- **Acción:** portar el patrón de `modules/compras/service.py` (`_mismo_payload` + `IdempotenciaConflicto`)
  a las otras 5, idealmente con un helper compartido.

### FF-2 — Cachear `config_empresa` (lecturas por turno)
- **Qué:** desde Fase 0, cada turno de venta carga `Umbrales`/`LimitesEmpresa` desde `config_empresa`
  (control DB) vía `ControlUmbralesStore`. Es +1 lectura por venta en el camino caliente (~60% bypass).
- **Riesgo:** bajo (perf). Por diseño la política por empresa necesita la config; hoy sin caché.
- **Acción:** cachear `config_empresa` por tenant con TTL (molde de `capacidades_cache` /
  `control_cache`, ya invalidados en tests). Reduce el round-trip a control DB por turno.

### FF-3 — `_mismo_payload` (compras) no compara proveedor por nombre/nit
- **Qué:** el guard de conflicto de `compras` compara proveedor solo cuando llega por `id` explícito
  (resolver nombre/nit crearía un proveedor antes de saber si es replay). Una key reusada con mismo
  ítems+total pero proveedor-por-nombre distinto se trata como replay (devuelve la original).
- **Riesgo:** muy bajo (edge); no inserta nada nuevo.
- **Acción:** si se requiere estricto, resolver proveedor sin efecto secundario y compararlo.

### FF-4 — Config de límites inválida se vuelve "sin tope" en silencio
- **Qué:** `limites_desde_overrides` (`ai/limites.py`) convierte un valor inválido o ≤0 en
  `venta_monto_max` / `venta_descuento_max_pct` a `None` = sin tope. Es fail-open a nivel de
  **configuración**: un typo al setear el límite lo desactiva sin avisar (el enforcement en runtime
  sigue siendo fail-closed; el hueco es que el tope podría no existir por un valor mal escrito).
- **Riesgo:** bajo. El default seguro de runtime no cambia; el riesgo es operativo (creer que hay
  tope cuando no).
- **Acción:** validar el valor al **setearlo** en `config_empresa` (`tools/set_config.py`): rechazar
  no-numérico/≤0 para esas claves, en vez de aceptarlo y degradarlo a "sin tope".

### FF-5 — Falso positivo del denylist de inyección en texto libre
- **Qué:** el patrón anti-inyección de `ai/saneamiento.py` corre sobre TODOS los campos de texto,
  incluidos los de texto libre legítimo (`descripcion` de venta varia, `concepto` de gasto). Una
  frase desafortunada podría dar match y bloquear la operación como `validacion` **no recuperable**.
- **Riesgo:** bajo (los patrones son de alta señal), pero el bloqueo es duro (no recuperable).
- **Acción:** monitorear logs `entrada_rechazada` por falsos positivos; considerar NO aplicar el
  patrón de inyección sobre el contenido de esos campos conocidos de texto libre (sí mantener el cap
  de longitud y el filtro de caracteres de control).
