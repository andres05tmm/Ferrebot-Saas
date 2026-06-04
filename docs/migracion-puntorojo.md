# Migración de FerreBot → Punto Rojo (tenant #1)

> Mapeo **a nivel de campo** del esquema actual de FerreBot (una sola tienda) a la **app DB del tenant** (ver `schema.md`). Es el primer aprovisionamiento (ver `onboarding-tenant.md`). Fuente: `ferrebot_export/` (schema.sql, columnas.csv, fks.txt, secuencias.txt, conteos.txt).
>
> **Importante:** el export reveló divergencias entre FerreBot real y el esquema objetivo de `schema.md`. Las que requieren ampliar el esquema destino están en §8 (**Brechas de esquema**). Resolverlas es prerequisito para la paridad.

## 1. Estrategia

Tres tratamientos por tabla (regla del audit, `architecture.md` §17):

- **Copiar (referencia/legal):** se trasladan datos tal cual (con transformación de tipos). Catálogo, clientes, histórico fiscal DIAN, saldos IVA, usuarios/config.
- **Reconstruir:** no se copia el detalle operativo; se siembra el **estado vivo**. Inventario (saldo inicial + kardex desde cero), caja (solo abierta), fiados (solo saldos), gastos.
- **Descartar/operativo:** datos efímeros de IA/voz/conversación; se recrean en uso. No migran (o migran solo si se quiere histórico).

Volumen actual (de `conteos.txt`): productos 632, productos_fracciones 722, ventas 228 / ventas_detalle 481, facturas_electronicas 111, clientes 59, compras_fiscal 26, bancolombia_transferencias 122. **fiados, inventario, compras, iva_saldos están vacíos hoy** (la migración de esos es trivial o solo estructura).

## 2. Transformaciones globales (aplican a todas las tablas)

| # | Regla | Origen (FerreBot) | Destino (tenant) |
|---|---|---|---|
| G1 | **Sin `empresa_id`** | tabla de una tienda | misma tabla; la base ES el tenant. No se agrega columna |
| G2 | **Dinero** | `integer` (pesos COP sin decimales) | `NUMERIC(12,2)` (mismo valor, `.00`; **no dividir**) |
| G3 | **Cantidades** | `numeric` | `NUMERIC(12,3)` (preserva fracciones) |
| G4 | **Fechas naive → UTC** | `timestamp without time zone` (hora local Colombia) | `TIMESTAMPTZ`: interpretar como **America/Bogota (UTC-5)** y guardar en UTC |
| G5 | **`fecha` + `hora` → 1 timestamp** | `date` + `time` separados | combinar en America/Bogota → `TIMESTAMPTZ` |
| G6 | **IDs** | `integer` | `BIGINT/BIGSERIAL`; **preservar los valores** (mantener PKs para no romper FKs) |
| G7 | **Consecutivos** | columna entera o embebida en texto | tras cargar, `SEQUENCE` por tenant con `setval(max)` (ver §6) |
| G8 | **Borrado lógico** | `activo`/`abierta` boolean | igual; no borrar histórico fiscal |

> **Verificar G4:** confirmar en qué zona escribió FerreBot las marcas naive (si el servidor corría en UTC, **no** sumar 5h). Decidir antes de cargar; es la transformación de mayor riesgo.

## 3. Clasificación de las 29 tablas de FerreBot

| Tabla origen | Filas | Tratamiento | Tabla destino |
|---|---|---|---|
| `productos` | 632 | Copiar | `productos` (+ ver pricing escalonado §8) |
| `productos_fracciones` | 722 | Copiar | `productos_fracciones` (**falta en destino, §8**) |
| `aliases` | 1 | Copiar | `aliases` (**falta en destino, §8**) |
| `inventario` | 0 | Reconstruir | `inventario` + `movimientos_inventario` (saldo inicial) |
| `clientes` | 59 | Copiar | `clientes` |
| `ventas` | 228 | Copiar (histórico opcional) | `ventas` |
| `ventas_detalle` | 481 | Copiar | `ventas_detalle` |
| `historico_ventas` | 33 | Copiar o reconstruir | `historico_ventas` (**falta en destino, §8**) |
| `compras` | 0 | Reconstruir | `compras` + `compras_detalle` |
| `compras_fiscal` | 26 | Copiar (legal) | `compras_fiscal` |
| `facturas_proveedores` | 1 | Copiar | `facturas_proveedores` (**falta en destino, §8**) |
| `facturas_abonos` | 1 | Copiar | `facturas_abonos` (**falta en destino, §8**) |
| `gastos` | 4 | Copiar | `gastos` (ver modelo caja §8) |
| `caja` | 2 | Reconstruir | `caja` + `caja_movimientos` (solo la abierta) |
| `fiados` | 0 | Reconstruir (saldos) | `fiados` |
| `fiados_movimientos` | 0 | Reconstruir | `fiados_movimientos` |
| `facturas_electronicas` | 111 | **Copiar (legal DIAN)** | `facturas_electronicas` |
| `documentos_soporte` | 1 | **Copiar (legal DIAN)** | `documentos_soporte` (**falta en destino, §8**) |
| `cuentas_cobro` | 1 | Copiar | `cuentas_cobro` (ver §8: sin `cliente_id`) |
| `iva_saldos_bimestrales` | 0 | Copiar | `iva_saldos_bimestrales` |
| `bancolombia_transferencias` | 122 | Copiar | `bancolombia_transferencias` (**falta en destino, §8**) |
| `usuarios` | 6 | Copiar | `usuarios` |
| `config` | 7 | Copiar | `config_empresa` |
| `ferrebot_config` | 0 | Copiar | `config_empresa` |
| `memoria_entidades` | 24 | Copiar (opcional) | `memoria_entidades` |
| `conversaciones_bot` | 71 | Descartar/operativo | `conversaciones_bot` (opcional) |
| `audio_logs` | 51 | Descartar/operativo | `audio_logs` (opcional) |
| `ventas_pendientes_voz` | 0 | Descartar | — |
| `api_costo_diario` | 24 | Descartar/operativo | `api_costo_diario` |
| `alembic_version` | 1 | **No migrar** | (lo gestiona el Alembic del tenant) |

## 4. Mapeo campo a campo (tablas clave)

Notación: `origen → destino  [transformación]`. Lo no listado se descarta o no tiene destino directo (marcado).

### 4.1 productos → productos

| FerreBot | Destino | Nota |
|---|---|---|
| `id` | `id` | preservar (G6) |
| `codigo` | `codigo` | UNIQUE; FerreBot permite null → si null, usar `clave` |
| `clave` | — | clave interna; si no hay `codigo`, vuélcala ahí o a `config`/alias |
| `nombre` | `nombre` | |
| `nombre_lower` | — | derivado; el destino indexa con trigram (no se copia) |
| `categoria` | `categoria` | |
| — | `marca` | FerreBot no tiene marca → null |
| `unidad_medida` | `unidad_medida` | default 'Unidad' |
| `precio_unidad` | `precio_venta` | G2 (int→numeric) |
| — | `precio_compra` | no existe en productos; viene de `inventario.ultimo_costo` (§4.2) |
| `precio_sobre_umbral`/`precio_bajo_umbral`/`precio_umbral` | `precio_mayorista` (aprox.) | **pricing escalonado, §8**: el destino solo tiene un precio mayorista; mapear `precio_bajo_umbral` o decidir regla |
| `tiene_iva` + `porcentaje_iva` | `iva` (SMALLINT) | `iva = porcentaje_iva if tiene_iva else 0` |
| `permite_fraccion`(implícito) | `permite_fraccion` | true si tiene filas en `productos_fracciones` |
| `aliases` (ARRAY) | → tabla `aliases` / `productos_fracciones` | el array de alias por producto va a la mecánica de búsqueda (§8) |
| `activo` | `activo` | |
| `created_at`/`updated_at` | `creado_en`/`actualizado_en` | G4 |

### 4.2 inventario → inventario (+ movimientos_inventario)

FerreBot no tiene kardex (no hay `movimientos_inventario`); `inventario` es un agregado.

| FerreBot | Destino | Nota |
|---|---|---|
| `producto_id` | `inventario.producto_id` | PK |
| `cantidad` | `inventario.stock_actual` | G3 |
| `minimo` | `inventario.stock_minimo` | G3 |
| `ultimo_costo` | `productos.precio_compra` | mover a productos |
| `costo_promedio`/`ultimo_proveedor`/`ultima_*` | — | no hay destino; opcional a `config`/notas |
| (sembrado) | `movimientos_inventario` | **1 movimiento `AJUSTE` por producto** = saldo inicial, `referencia='migracion'`. Cumple "stock solo cambia con kardex" |

> Hoy `inventario` está vacío (0 filas): solo se siembra el saldo inicial cuando exista stock real.

### 4.3 clientes → clientes

| FerreBot | Destino | Nota |
|---|---|---|
| `id` | `id` | |
| `nombre` | `nombre` | |
| `tipo_id` | `tipo_documento` | mapear código FerreBot → {CC, NIT, CE} |
| `identificacion` | `documento` | |
| `tipo_persona` | — | (natural/jurídica) opcional a `regimen` o config |
| `correo` | `correo` | |
| `telefono` | `telefono` | |
| `direccion` | `direccion` | |
| `regimen_fiscal` (int, def 2) | `regimen` (text) | mapear catálogo DIAN int → etiqueta |
| `municipio_dian` (int, def 149) | `ciudad_dane` | **OJO:** es ID DIAN/MATIAS, **no** código DANE. Reconciliar con la caché `_get_city_id` (`facturacion-dian.md`); decidir si `ciudad_dane` guarda DANE o el id MATIAS |
| `pais_id` (def 45) | — | opcional a config fiscal |
| `ciudad_nombre` | — | informativo; opcional |
| (derivado) | `saldo_fiado` | calcular desde `fiados`/`fiados_movimientos` (no hay columna en origen) |
| `created_at` | `creado_en` | G4 |

### 4.4 ventas → ventas

| FerreBot | Destino | Nota |
|---|---|---|
| `id` | `id` | |
| `consecutivo` | `consecutivo` | preservar; `SEQUENCE` con `setval(max)` (§6) |
| `fecha` + `hora` | `fecha` | G5 (combinar → TIMESTAMPTZ) |
| `cliente_id` | `cliente_id` | |
| `cliente_nombre` | — | denormalizado; el destino lo resuelve por FK |
| `usuario_id` | `vendedor_id` | FK usuarios |
| `vendedor` (texto) | — | denormalizado; usar `usuario_id` |
| `metodo_pago` (varchar) | `metodo_pago` (enum) | mapear a {efectivo, transferencia, tarjeta, nequi, daviplata, fiado}; `datafono`→`tarjeta` |
| `total` | `total` | G2 |
| (derivar) | `subtotal`, `impuestos` | FerreBot no separa IVA en venta → calcular desde `ventas_detalle` (o `impuestos=0` y `subtotal=total` si no se puede; **decisión**) |
| (constante) | `estado` | FerreBot no marca anuladas → `'completada'` |
| (constante) | `origen` | histórico → `'web'` (o `'bot'`); sin dato real |
| `factura_numero`/`factura_cufe`/`factura_estado`/`facturada_at` | — | la relación va por `facturas_electronicas.venta_id`; conservar como verificación cruzada |
| (nuevo) | `idempotency_key` | null en histórico |

### 4.5 ventas_detalle → ventas_detalle

| FerreBot | Destino | Nota |
|---|---|---|
| `id` | `id` | |
| `venta_id` | `venta_id` | FK CASCADE |
| `producto_id` | `producto_id` | null = venta varia |
| `producto_nombre` | `descripcion` | usar para ítems sin `producto_id` |
| `cantidad` | `cantidad` | G3 |
| `precio_unitario` | `precio_unitario` | G2 |
| `total` | — | derivado (cantidad×precio); el destino no guarda total de línea |
| `unidad_medida` | — | informativo |
| `alias_usado` | — | a histórico de IA si se quiere |
| `sin_detalle` | — | flag operativo |
| (derivar) | `iva` | del producto (`productos.iva`) al momento, o 0 |

### 4.6 facturas_electronicas → facturas_electronicas  (LEGAL — preservar)

| FerreBot | Destino | Nota |
|---|---|---|
| `id` | `id` | |
| `venta_id` | `venta_id` | FK SET NULL |
| `numero` (varchar, p.ej. `FE-1234`) | `prefijo` + `consecutivo` | **separar** prefijo/número; preservar exacto |
| `cufe` | `cufe` | preservar (legal) |
| `tipo` (def 'factura') | `tipo` (enum) | {factura, documento_soporte, nota_credito, nota_debito} |
| `estado` (def 'emitida') | `estado` (enum fe_estado) | `'emitida'→'aceptada'`; mapear resto |
| `cliente_nombre` | — | resolver por venta/cliente |
| `total` | — | el destino no guarda total en FE (está en la venta); opcional a `dian_respuesta` |
| `error_msg` | `dian_respuesta` (jsonb) | envolver `{ "error_msg": ... }` |
| `razon_id` | — | razón de nota; relevante para `notas_electronicas` |
| `factura_cufe_ref` | (→ `notas_electronicas.cufe` ref) | si la fila es una nota, referencia la factura original |
| `fecha_emision` | `emitido_en` | G4 |
| `created_at` | `creado_en` | G4 |
| (nuevo) | `xml_url`, `pdf_url` | null si no se conservaron archivos |
| (nuevo) | `idempotency_key`, `intentos` | null / 0 en histórico |

> **Continuidad DIAN (crítico):** el nuevo `SEQUENCE` de factura debe arrancar en `max(consecutivo)` real (no en el id_seq=192, que es la PK). Igual para DS. Ver §6.

### 4.7 documentos_soporte → documentos_soporte  (LEGAL)

| FerreBot | Destino | Nota |
|---|---|---|
| `id`, `consecutivo` | igual | preservar |
| `cude` | `cude` | **DS usa CUDE, no CUFE** |
| `fecha`, `valor` | `fecha`, `valor` | G2/G4 |
| `estado_dian` | `estado` | mapear |
| `cuenta_cobro_id` | `cuenta_cobro_id` | FK |

### 4.8 compras_fiscal → compras_fiscal  (LEGAL)

Copiar tal cual (preservar `cufe_proveedor`, eventos 030/031/032/033, `evento_estado`, vinculación). Dinero G2, fechas G4. Mantener `compra_origen_id`. (Tabla rica; ver `columnas.csv` para las 26 columnas.)

### 4.9 fiados / fiados_movimientos → idem

Vacías hoy → solo estructura. Mapeo cuando haya datos: `saldo_actual→saldo`, `cliente_nombre`→FK cliente; movimientos `cargo`/`abono`/`saldo_resultante` → `fiados_movimientos` (tipo cargo/abono). El `saldo_fiado` del cliente se recalcula desde aquí.

### 4.10 Resto (resumen)

- **gastos → gastos:** `concepto`, `monto`(G2), `categoria` (mapear a enum gasto_categoria), `usuario_id`. **Sin `caja_id` en origen** → reconstruir el vínculo a caja (§8).
- **caja → caja + caja_movimientos:** modelo distinto (agregado diario vs apertura/cierre con arqueo). Solo migrar la caja **abierta** (si la hay) como apertura; el histórico va a `historico_ventas`.
- **cuentas_cobro → cuentas_cobro:** `consecutivo`, `numero_display`, `periodo`, `concepto`, `valor`(G2), `pdf_bytes` (bytea → mover a Cloudinary y guardar URL, o conservar). **Sin `cliente_id`** en origen (§8).
- **iva_saldos_bimestrales:** `año→anio`, `iva_ventas→iva_generado`, `iva_compras→iva_descontable`, `iva_neto`/`saldo_anterior→saldo`, `estado`. Vacía hoy.
- **usuarios → usuarios:** map directo; `rol` varchar→enum {admin, vendedor}; `telegram_id` UNIQUE. 6 filas.
- **config + ferrebot_config → config_empresa:** `clave`→PK, `valor` (text)→`valor` (jsonb, envolver). Unificar ambas; resolver claves duplicadas.
- **memoria_entidades:** `entidad_key→clave`, `nota→valor`(jsonb), conservar `tipo`. Opcional.
- **bancolombia_transferencias, facturas_proveedores, facturas_abonos, historico_ventas, productos_fracciones, aliases:** copiar a tablas homónimas **que hay que crear en el destino** (§8).
- **conversaciones_bot, audio_logs, api_costo_diario, ventas_pendientes_voz:** operativos de IA; migrar solo si se quiere histórico (no aportan al estado del negocio).

## 5. Orden de carga (respeta FKs)

De `fks.txt`. Cargar en este orden:

1. `usuarios`, `productos`, `clientes`, `config_empresa`
2. `productos_fracciones`, `inventario` (+ `movimientos_inventario` sembrados)
3. `proveedores` (derivar de textos, §8), `facturas_proveedores`
4. `ventas` → `ventas_detalle`
5. `facturas_electronicas` → `notas` ; `cuentas_cobro` → `documentos_soporte`
6. `compras_fiscal` → `compras`
7. `fiados` → `fiados_movimientos`; `facturas_abonos`
8. `gastos`, `caja`(+movimientos), `historico_ventas`, `iva_saldos_bimestrales`
9. `bancolombia_transferencias`, `memoria_entidades`, (operativos opcionales)
10. **`SEQUENCE setval`** de todo (§6)

## 6. Secuencias y consecutivos (post-carga)

Tras insertar preservando IDs, **avanzar cada `SEQUENCE`** al máximo para que los nuevos inserts no choquen:

```sql
SELECT setval(pg_get_serial_sequence('productos','id'),  (SELECT max(id) FROM productos));
SELECT setval(pg_get_serial_sequence('ventas','id'),      (SELECT max(id) FROM ventas));
-- ... cada tabla con BIGSERIAL
-- Consecutivos de negocio (no son la PK):
SELECT setval('venta_consecutivo_seq',  (SELECT max(consecutivo) FROM ventas));
SELECT setval('factura_consecutivo_seq',(SELECT max(consecutivo) FROM facturas_electronicas WHERE tipo='factura'));
SELECT setval('ds_consecutivo_seq',     (SELECT max(consecutivo) FROM documentos_soporte));
```

> El `last_value` actual está en `secuencias.txt` (p. ej. `facturas_electronicas_id_seq=192`, `ventas_id_seq=430`). Recuerda: esos son **id de PK**, no los consecutivos legales. El consecutivo legal de factura está embebido en `numero`; extraer su máximo y continuar desde ahí.

## 7. Procedimiento (encaja con el provisioning)

Es el aprovisionamiento de `tenancy.md` §8 con un paso extra de carga:

1. `provision_tenant(slug='puntorojo', ...)` → crea base, `alembic upgrade head` (tenant), siembra base, secretos (MATIAS/Cloudinary/bot), branding, admin.
2. **Carga ETL** (script idempotente, por lotes, en el orden §5) leyendo el dump/origen de FerreBot.
3. `setval` de secuencias (§6).
4. **Validación de paridad** (§9).
5. Portar servicio MATIAS (caché `_get_city_id`), bypass y `ai/tools.py`.
6. Conectar bot y dashboard; smoke test; corte de webhooks.

El ETL es **idempotente** (re-ejecutable): upsert por PK preservada; nunca duplica histórico fiscal.

## 8. Brechas de esquema (resolver ANTES de codificar)

El export muestra tablas/campos de FerreBot **sin destino** en `schema.md`. Para paridad real hay que ampliar el esquema del tenant (o decidir descartar). Pendiente de decisión:

| Brecha | FerreBot | Decisión necesaria |
|---|---|---|
| **Fracciones de precio** | `productos_fracciones` (722 filas: fracción → precio) | Crear tabla `productos_fracciones` en destino. `schema.md` solo tiene `permite_fraccion` bool: **insuficiente** |
| **Pricing escalonado** | `productos.precio_umbral/bajo/sobre` | El destino solo modela `precio_mayorista`. ¿Se adopta el umbral o se simplifica? |
| **Aliases de búsqueda** | tabla `aliases` + `productos.aliases[]` + `ventas_detalle.alias_usado` | Crear soporte de aliases (lo usa el bypass/IA, `ai-tools.md`) |
| **Conciliación bancaria** | `bancolombia_transferencias` (122) | Integración Bancolombia/Gmail sigue viva (`architecture.md` §12); falta la tabla en `schema.md` |
| **Cuentas por pagar** | `facturas_proveedores` + `facturas_abonos` | Modelo de deuda a proveedores no está en `schema.md` |
| **Rollup diario** | `historico_ventas` (33) | ¿Tabla de reporte o se reconstruye desde `ventas`? |
| **Documento soporte** | `documentos_soporte` (con CUDE) | `schema.md` lo pliega en `facturas_electronicas`; FerreBot lo tiene aparte. Decidir |
| **Gasto ↔ caja** | `gastos` sin `caja_id` | El destino exige que todo gasto mueva caja. Definir cómo se reconstruye |
| **Modelo de caja** | agregado diario vs apertura/arqueo | Confirmar el modelo destino y cómo migra el histórico |
| **Honorarios sin cliente** | `cuentas_cobro` sin `cliente_id` | El destino pone `cliente_id` NOT NULL implícito; FerreBot no lo tiene. Hacer nullable |
| **Proveedores** | no hay tabla; son texto libre | Crear `proveedores` y derivar del texto, o mantener texto |
| **api_costo_diario** | granular (vendedor, cache tokens) | El destino es más simple; ampliar si se quiere paridad |

## 9. Validación de paridad (tests de migración)

- **Conteos:** filas migradas = filas origen por tabla (salvo operativas descartadas a propósito).
- **Sumas de control:** `sum(ventas.total)`, `sum(facturas.total)`, saldos de fiados y de IVA coinciden origen↔destino.
- **Continuidad DIAN:** `max(consecutivo)` de factura/DS preservado; el siguiente emitido es `max+1`. CUFEs/CUDEs intactos.
- **FKs:** ninguna venta/detalle/factura queda huérfana tras preservar IDs.
- **Fechas (G4):** muestra de timestamps revisada (la venta de tal fecha/hora cae en el día correcto en hora Colombia).
- **Smoke:** una venta nueva, una emisión de factura de prueba, un cierre de caja en el tenant ya migrado.
- **Aislamiento:** correr el test de `.claude/rules/testing.md` (empresa A nunca ve B) con Punto Rojo como tenant real.

> Mantener `ferrebot_export/` fuera de git si contiene rutas/datos sensibles (revisar `.gitignore`). El dump es solo-estructura, pero por higiene no se versiona junto al código.
