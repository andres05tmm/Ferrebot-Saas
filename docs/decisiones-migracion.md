# Decisiones de migración + spec del ETL (Punto Rojo)

> Decisiones cerradas para la migración de FerreBot → tenant #1 y la **especificación del script ETL** que las implementa. Mapeo campo a campo en `migracion-puntorojo.md`; esquema destino en `schema.md`; lógica a portar en `ferrebot-logica-portar.md` y `facturacion-matias-extract.md`.

## 1. Decisiones cerradas

| # | Tema | Decisión | Impacto |
|---|---|---|---|
| D1 | **Modelo de precios** | Punto Rojo conserva el modelo FerreBot: simple + escalonado por umbral (`precio_umbral/bajo/sobre`) + `productos_fracciones`. Otros tenants podrán usar solo `precio_venta`/`precio_mayorista`. | `schema.md` ya incluye las columnas y la tabla de fracciones |
| D2 | **Documento Soporte** | Tabla **aparte** `documentos_soporte` (CUDE, resolución y consecutivo propios), no plegado en `facturas_electronicas`. | `schema.md` §Facturación DIAN |
| D3 | **Cuentas por pagar** | **Entran a v1**: `facturas_proveedores` + `facturas_abonos` (deuda a proveedor y abonos). | `schema.md` §Cuentas por pagar |
| D4 | **Conciliación bancaria** | **Entra a v1**: `bancolombia_transferencias` (ingesta por Gmail, idempotente por `gmail_message_id`). | `schema.md` §Conciliación bancaria |
| D5 | **Zona horaria** | **Colombia (UTC-5)**. Los `timestamp without time zone` de FerreBot se interpretan como hora local Colombia y se convierten a UTC al cargar. | Transformación G4 (§3) |
| D6 | **Histórico de ventas** | Se **copia** `historico_ventas` tal cual (rollup diario). **No** se derivan subtotal/IVA de las ventas históricas: `impuestos=0`, `subtotal=total`. | Tabla `historico_ventas`; regla T-VENTAS (§4) |
| D7 | **Cuenta de cobro sin cliente** | `cuentas_cobro.cliente_id` es **NULL** (la CC es del operador). | `schema.md` |
| D8 | **Dinero** | `integer` (pesos) → `NUMERIC(12,2)`, mismo valor, sin dividir. | Transformación G2 |
| D9 | **Consecutivos legales** | Preservar el consecutivo embebido en `facturas_electronicas.numero` y `documentos_soporte.consecutivo`; las nuevas SEQUENCE arrancan en el máximo real. | §5 + `facturacion-matias-extract.md` §6 |
| D10 | **Proveedores** | Se **deriva** una tabla `proveedores` desde los textos libres (`compras.proveedor`, `facturas_proveedores.proveedor`); el texto original se conserva como respaldo. | Paso ETL 3 (§4) |
| D11 | **Datos operativos de IA** | `conversaciones_bot`, `audio_logs`, `api_costo_diario`, `ventas_pendientes_voz`, `memoria_entidades` **no** migran (se recrean en uso). `memoria_entidades` opcional si se quiere arranque tibio. | §4 |
| D12 | **Arqueo de caja: `saldo_esperado` híbrido** | `saldo_esperado = saldo_inicial + ventas_efectivo + Σ ingresos − Σ egresos`. Las **ventas en efectivo se leen de la tabla `ventas`** (ventana de la caja, ese vendedor, `metodo_pago='efectivo'`, `completada`); ingresos/egresos **manuales y gastos** salen de `caja_movimientos`. **Anti-doble-conteo:** el gasto cuenta una sola vez como egreso (`caja_movimientos` es fuente única; la tabla `gastos` no se resta aparte). | `modules/caja/arqueo.py`; reproduce §6 (`caja_service.py:153`) sin tocar el slice de ventas |

> Pendiente de confirmar (no bloquea el ETL, sí el corte final): **zona real con la que el servidor de FerreBot escribió los timestamps naive** (si corría en UTC, D5 no suma 5h). Verificar con una muestra (§6).

> **Deuda nombrada — `wiring venta-efectivo → caja_movimientos ingreso`** (fase cross-módulo): hoy la venta en efectivo **no** postea un `caja_movimientos` ingreso; el arqueo la lee de la tabla `ventas` (D12). Cuando se conecte la venta a la caja (un ingreso por venta efectivo dentro de la caja abierta del vendedor), el `saldo_esperado` debe pasar a leerse **solo** de `caja_movimientos` y dejar de leer la tabla `ventas` para **no** doble-contar. No tocar el slice de ventas hasta esa fase.

## 2. Principios del ETL

- **Idempotente y re-ejecutable:** upsert por **PK preservada** (ON CONFLICT DO NOTHING / DO UPDATE). Correrlo dos veces no duplica ni corrompe.
- **Por lotes y transaccional por tabla:** cada tabla en su transacción; si una falla, se reporta y se continúa con las demás (no aborta todo).
- **Preserva IDs** (G6) para no romper FKs; al final, `setval` de todas las secuencias (§5).
- **Solo lectura sobre FerreBot:** el ETL nunca escribe en la base origen. Lee del dump o de una réplica.
- **Trazable:** log por tabla con filas leídas / insertadas / saltadas / con error, y sumas de control (§6).
- **Corre como job** del provisioning (paso extra tras `provision_tenant`, ver `tenancy.md` §8).

## 3. Transformaciones globales (recordatorio operativo)

| Id | Regla | Implementación |
|---|---|---|
| G2 | dinero int → NUMERIC(12,2) | `Decimal(valor)` sin dividir |
| G3 | cantidades → NUMERIC(12,3) | preservar fracciones |
| G4 | naive → UTC (Colombia) | `tz.localize(ts, 'America/Bogota').astimezone(utc)` |
| G5 | `fecha`+`hora` → 1 TIMESTAMPTZ | combinar y aplicar G4 |
| G6 | preservar PKs | insertar `id` explícito |
| G7 | secuencias | `setval(max)` post-carga (§5) |

## 4. Orden de ejecución del ETL (con reglas por tabla)

Respeta FKs (de `fks.txt`). Cada paso indica origen → destino y su regla.

1. **Referencia base**
   - `usuarios` → `usuarios` (`rol` varchar→enum {admin,vendedor}; `telegram_id` UNIQUE).
   - `config` + `ferrebot_config` → `config_empresa` (`valor` text → jsonb; unificar, resolver claves duplicadas; secretos NO van aquí, van a `secretos_empresa`).
   - `productos` → `productos` (**T-PRECIO:** `precio_unidad→precio_venta`; `tiene_iva+porcentaje_iva→iva`; umbral `precio_umbral/bajo/sobre` directo; `permite_fraccion = existe en productos_fracciones`; `aliases[]` → tabla `aliases`).
   - `clientes` → `clientes` (`identificacion→documento`; `tipo_id→tipo_documento`; `regimen_fiscal int→regimen`; `municipio_dian` se conserva como **DANE** en `ciudad_dane` — la resolución a id MATIAS la hace el servicio en runtime, no el ETL; `saldo_fiado` se calcula en el paso 7).
2. **Catálogo dependiente**
   - `productos_fracciones` → `productos_fracciones` (FK producto_id).
   - `aliases` (tabla) + `productos.aliases[]` → `aliases` (dedupe por `termino`).
   - `inventario` → `inventario` (**T-STOCK:** `cantidad→stock_actual`, `minimo→stock_minimo`, `ultimo_costo→productos.precio_compra`) **+ sembrar 1 `movimientos_inventario` AJUSTE** por producto con stock>0, `referencia='migracion'`. *(Hoy `inventario` está vacío → normalmente no genera filas.)*
3. **Proveedores (derivado — D10)**
   - `DISTINCT proveedor` de `compras` ∪ `facturas_proveedores` → `proveedores` (nombre). Guardar mapa `texto→proveedor_id` para los pasos 6 y de cuentas por pagar.
   - `facturas_proveedores` → `facturas_proveedores`; `facturas_abonos` → `facturas_abonos` (recalcular `pendiente`).
4. **Ventas**
   - `ventas` → `ventas` (**T-VENTAS:** `fecha`+`hora`→`fecha` TIMESTAMPTZ; `usuario_id→vendedor_id`; `metodo_pago` varchar→enum, `datafono→tarjeta`; `total→total`; `subtotal=total`, `impuestos=0` (D6); `estado='completada'`; `origen='web'`; `idempotency_key=NULL`; preservar `consecutivo`).
   - `ventas_detalle` → `ventas_detalle` (`producto_nombre→descripcion` si sin `producto_id`; `iva` del producto al momento o 0).
   - `historico_ventas` → `historico_ventas` (copia directa, G2; D6).
5. **Facturación (legal — preservar)**
   - `facturas_electronicas` → `facturas_electronicas` (**T-FE:** separar `numero`→`prefijo`+`consecutivo`; `estado 'emitida'→'aceptada'`; preservar `cufe`; `tipo`; `razon_id`/`factura_cufe_ref` para notas; `error_msg→dian_respuesta.jsonb`).
   - `cuentas_cobro` → `cuentas_cobro` (D7: `cliente_id` NULL; `pdf_bytes`→subir a Cloudinary y guardar URL, o conservar temporal).
   - `documentos_soporte` → `documentos_soporte` (preservar `cude`, `consecutivo`, `estado_dian`; FK `cuenta_cobro_id`).
6. **Compras / caja / gastos**
   - `compras_fiscal` → `compras_fiscal` (legal; preservar `cufe_proveedor`, eventos 030-033, `evento_estado`).
   - `compras` → `compras` + `compras_detalle` (reconstruir detalle; vincular `proveedor_id` del mapa del paso 3). *(Hoy vacía.)*
   - `gastos` → `gastos` (**T-GASTO:** `categoria` varchar→enum; reconstruir vínculo a caja si aplica).
   - `caja` → `caja` (**T-CAJA:** solo la caja **abierta** se migra como apertura; el histórico diario ya está en `historico_ventas`).
7. **Fiados (saldos vivos)**
   - `fiados` → `fiados`; `fiados_movimientos` → `fiados_movimientos` (recalcular `saldo`); actualizar `clientes.saldo_fiado` desde los movimientos. *(Hoy vacías.)*
8. **Conciliación / operativos (opcional)**
   - `bancolombia_transferencias` → idem (idempotente por `gmail_message_id`).
   - `iva_saldos_bimestrales` → idem (`año→anio`, `iva_ventas→iva_generado`, `iva_compras→iva_descontable`). *(Hoy vacía.)*
   - `memoria_entidades` → opcional (`entidad_key→clave`, `nota→valor` jsonb).
   - **No migrar:** `conversaciones_bot`, `audio_logs`, `api_costo_diario`, `ventas_pendientes_voz`, `alembic_version` (D11).
9. **`setval` de todas las secuencias** (§5).
10. **Validación de paridad** (§6).

## 5. Secuencias (post-carga)

```sql
-- PKs (BIGSERIAL) — repetir por cada tabla con id serial:
SELECT setval(pg_get_serial_sequence('productos','id'), COALESCE((SELECT max(id) FROM productos),1));
SELECT setval(pg_get_serial_sequence('ventas','id'),     COALESCE((SELECT max(id) FROM ventas),1));
-- … (todas las tablas con id serial)

-- Consecutivos de negocio (NO son la PK):
SELECT setval('venta_consecutivo_seq',   COALESCE((SELECT max(consecutivo) FROM ventas),0));
SELECT setval('factura_consecutivo_seq', COALESCE((SELECT max(consecutivo) FROM facturas_electronicas WHERE tipo='factura'),0));
SELECT setval('ds_consecutivo_seq',      COALESCE((SELECT max(consecutivo) FROM documentos_soporte),0));
```

> El consecutivo legal de factura/DS vive **embebido en texto** (`numero`/`consecutivo`); extraer el máximo numérico con la misma regex que usa FerreBot (`facturacion-matias-extract.md` §6). El piso es `MATIAS_NUM_DESDE` / `MATIAS_DS_NUM_DESDE`.

## 6. Validación de paridad (gate de corte)

Tests obligatorios antes de apuntar webhooks al nuevo sistema:

- **Conteos:** filas destino = filas origen por tabla (salvo las descartadas a propósito en D11).
- **Sumas de control:** `Σ ventas.total`, `Σ facturas_electronicas.total`, saldos de fiados, IVA bimestral y `historico_ventas` coinciden origen↔destino.
- **Continuidad DIAN:** `max(consecutivo)` de factura y DS preservados; el siguiente emitido = `max+1`; CUFE/CUDE intactos.
- **FKs:** cero huérfanos (venta_detalle→venta, factura→venta, ds→cuenta_cobro, abonos→factura_proveedor).
- **Fechas (D5):** muestra de timestamps revisada en hora Colombia (una venta de las 7 PM cae el día correcto, no el siguiente). **Si fallan, el servidor escribía en UTC → quitar el +5h.**
- **Smoke en el tenant migrado:** una venta nueva, una emisión de factura de prueba (sandbox MATIAS), un cierre de caja, un abono a fiado.
- **Aislamiento:** test de `.claude/rules/testing.md` con Punto Rojo como tenant real (no ve datos de otro tenant).

## 7. Estructura sugerida del script

```
tools/etl_puntorojo/
├── __main__.py        # orquesta: lee origen, corre pasos en orden (§4), reporta
├── extract.py         # lectura del dump/réplica FerreBot (solo lectura)
├── transform.py       # G2-G5 + T-PRECIO/T-VENTAS/T-FE/… (funciones puras, testeables)
├── load.py            # upsert idempotente por PK al tenant (repositorios)
├── sequences.py       # setval (§5)
└── verify.py          # paridad (§6); falla con código ≠ 0 si algo no cuadra
```

- `transform.py` son **funciones puras** (entrada dict origen → dict destino) con tests unitarios por regla.
- `load.py` usa la sesión del **tenant** (`get_tenant_db()`), nunca una global.
- Se invoca tras `provision_tenant('puntorojo')`; es **idempotente**, así que un reintento es seguro.
