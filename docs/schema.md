# Esquema de datos (detallado)

> Esquema físico para generar los modelos SQLAlchemy y las migraciones Alembic.
> Mapa conceptual en `data-model.md`. Convenciones de tenancy en `tenancy.md`.

## Convenciones

- Tipos: `BIGSERIAL`/`BIGINT` para IDs; `NUMERIC(12,2)` para dinero; `NUMERIC(12,3)` para cantidades (permite fracciones); `TIMESTAMPTZ` para fechas (siempre UTC en disco, se muestra en hora Colombia); `JSONB` para estructuras flexibles; `BYTEA` para cifrados.
- Toda tabla lleva `creado_en TIMESTAMPTZ NOT NULL DEFAULT now()`; las mutables, `actualizado_en`.
- **App DB:** las tablas de negocio NO llevan `empresa_id` (la base es la frontera del tenant).
- **Consecutivos** (ventas, facturas) salen de una `SEQUENCE` por tenant, no de `MAX()+1`.
- **Idempotencia:** operaciones críticas llevan `idempotency_key TEXT UNIQUE`.
- Borrado lógico con `activo BOOLEAN` donde aplique; no borrar histórico fiscal.

---

## Enums

| Enum | Valores |
|---|---|
| `tenant_estado` | provisionando, activa, suspendida, vencida |
| `suscripcion_estado` | prueba, activa, suspendida, vencida |
| `global_rol` | super_admin |
| `usuario_rol` | admin, vendedor |
| `mov_inventario_tipo` | ENTRADA, SALIDA, AJUSTE, DEVOLUCION |
| `venta_estado` | completada, anulada |
| `venta_origen` | web, bot, voz, offline |
| `metodo_pago` | efectivo, transferencia, tarjeta, nequi, daviplata, fiado |
| `caja_estado` | abierta, cerrada |
| `caja_mov_tipo` | ingreso, egreso |
| `gasto_categoria` | transporte, papeleria, servicios, nomina, mantenimiento, otros |
| `fiado_mov_tipo` | cargo, abono |
| `fe_tipo` | factura, documento_soporte, nota_credito, nota_debito |
| `fe_estado` | pendiente, enviada, aceptada, rechazada, error |

---

## Control DB (plano de control)

### empresas
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| nombre | TEXT | NOT NULL |
| nit | TEXT | NOT NULL, UNIQUE |
| slug | TEXT | NOT NULL, UNIQUE (subdominio) |
| estado | tenant_estado | NOT NULL, DEFAULT 'provisionando' |
| plan_id | BIGINT | FK planes(id) |
| creado_en | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

### tenant_databases
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| empresa_id | BIGINT | PK, FK empresas(id) |
| db_name | TEXT | NOT NULL (nombre de la base) |
| host | TEXT | NOT NULL (instancia/cluster) |
| connection_url_cifrada | BYTEA | NOT NULL (credencial cifrada) |
| region | TEXT | |

### planes
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| nombre | TEXT | NOT NULL |
| limites | JSONB | { usuarios_max, facturas_mes_max, modulos[] } |
| precio_mensual | NUMERIC(12,2) | |

### suscripciones
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| empresa_id | BIGINT | FK empresas(id) |
| plan_id | BIGINT | FK planes(id) |
| estado | suscripcion_estado | NOT NULL, DEFAULT 'activa' |
| periodo_inicio | DATE | |
| periodo_fin | DATE | |

### secretos_empresa
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| empresa_id | BIGINT | FK empresas(id) |
| clave | TEXT | NOT NULL (ej: matias_password, telegram_token) |
| valor_cifrado | BYTEA | NOT NULL (AEAD) |
| nonce | BYTEA | NOT NULL |
| actualizado_en | TIMESTAMPTZ | |
| | | UNIQUE(empresa_id, clave) |

### branding
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| empresa_id | BIGINT | PK, FK empresas(id) |
| logo_url | TEXT | |
| color_primario | TEXT | DEFAULT '#C8200E' |
| nombre_comercial | TEXT | |
| dominio | TEXT | dominio propio opcional |

### empresa_features
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| empresa_id | BIGINT | FK empresas(id) |
| feature | TEXT | NOT NULL (clave del catálogo; ver feature-flags.md) |
| habilitada | BOOLEAN | NOT NULL (override sobre el plan) |
| | | UNIQUE(empresa_id, feature) |

> Capacidades efectivas = features del `plan` ± overrides aquí. `planes.limites.features` trae el set por defecto.

### config_empresa
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| empresa_id | BIGINT | NOT NULL, FK empresas(id) |
| clave | TEXT | NOT NULL (ej: `llm_provider`, `bypass_monto_max`, parámetros DIAN no secretos) |
| valor | TEXT | NOT NULL (valor en claro; los secretos van cifrados en `secretos_empresa`) |
| actualizado_en | TIMESTAMPTZ | DEFAULT now() |
| | | UNIQUE(empresa_id, clave) |

> **Config no-secreta por empresa vive en el control DB** (decisión Fase 8), no en la app DB: se siembra en el
> provisioning junto a la empresa y se carga **una vez** en el contexto cacheado del tenant (como las capacidades,
> `tenancy.md` §3), para que los hot paths (bypass) no paguen round-trip. Implementado en
> `migrations/control/0002_config_empresa`.

### super_admins
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| email | TEXT | NOT NULL, UNIQUE |
| nombre | TEXT | |
| password_hash | TEXT | NOT NULL |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

---

## App DB por empresa (esquema de negocio)

### Catálogo e inventario

**productos**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| codigo | TEXT | UNIQUE (código de barras / SKU) |
| nombre | TEXT | NOT NULL |
| categoria | TEXT | |
| proveedor_id | BIGINT | FK proveedores(id) ON DELETE SET NULL, NULLABLE (tenant 0006; reemplaza la antigua `marca`) |
| unidad_medida | TEXT | NOT NULL (unidad, metro, kg…) |
| precio_venta | NUMERIC(12,2) | NOT NULL |
| precio_compra | NUMERIC(12,2) | |
| precio_especial | NUMERIC(12,2) | precio especial (tenant 0006; antes `precio_mayorista`) |
| precio_umbral | NUMERIC(12,3) | cantidad umbral del precio escalonado (modelo FerreBot); NULL si no aplica |
| precio_bajo_umbral | NUMERIC(12,2) | precio unidad por debajo del umbral |
| precio_sobre_umbral | NUMERIC(12,2) | precio unidad en/por encima del umbral (mayoreo por cantidad) |
| iva | SMALLINT | NOT NULL, DEFAULT 19 (0/5/19) |
| permite_fraccion | BOOLEAN | NOT NULL, DEFAULT false |
| activo | BOOLEAN | NOT NULL, DEFAULT true |
| creado_en / actualizado_en | TIMESTAMPTZ | |

Índices: `UNIQUE(codigo)`; GIN/trigram sobre `nombre` (búsqueda fuzzy/FTS).

> **Precios (Punto Rojo = modelo FerreBot).** Tres esquemas que conviven (ver `ferrebot-logica-portar.md` §3): (1) `precio_venta` simple; (2) escalonado por cantidad con `precio_umbral`/`precio_bajo_umbral`/`precio_sobre_umbral`; (3) por fracción en `productos_fracciones`. Un tenant simple usa solo `precio_venta`/`precio_especial` y deja lo demás en NULL. El alta de producto NO recibe stock: el inventario nace en 0 (stock y mínimo) y el stock real se fija con el conteo físico.

**productos_fracciones** (precio por fracción: 1/2, 1/4…)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| producto_id | BIGINT | FK productos(id) ON DELETE CASCADE, NOT NULL |
| fraccion | TEXT | NOT NULL (ej: '1/4', '1/2') |
| decimal | NUMERIC(12,3) | valor decimal de la fracción (0.25, 0.5) |
| precio_total | NUMERIC(12,2) | NOT NULL (precio de esa fracción) |
| precio_unitario | NUMERIC(12,2) | |
| | | UNIQUE(producto_id, fraccion) |

**aliases** (variantes/typos → producto; alimenta búsqueda y bypass)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| termino | TEXT | NOT NULL (lo que escribe el vendedor; ej: 'drwayll') |
| reemplazo | TEXT | NOT NULL (forma canónica; ej: 'drywall') |
| producto_id | BIGINT | FK productos(id), NULL (alias global si NULL) |
| creado_en / actualizado_en | TIMESTAMPTZ | |
| | | UNIQUE(termino) |

> Gobernado por la feature `bot_telegram`/búsqueda. En FerreBot el alias también vive como array en `productos.aliases`; aquí se normaliza a tabla.

**inventario**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| producto_id | BIGINT | PK, FK productos(id) |
| stock_actual | NUMERIC(12,3) | NOT NULL, DEFAULT 0 |
| stock_minimo | NUMERIC(12,3) | NOT NULL, DEFAULT 0 |
| actualizado_en | TIMESTAMPTZ | |

**movimientos_inventario** (kardex)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| producto_id | BIGINT | FK productos(id), NOT NULL |
| tipo | mov_inventario_tipo | NOT NULL |
| cantidad | NUMERIC(12,3) | NOT NULL (siempre positiva; el tipo da el signo) |
| costo_unitario | NUMERIC(12,2) | |
| referencia | TEXT | ej: 'venta:123', 'compra:45' |
| usuario_id | BIGINT | FK usuarios(id) |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

Índice: `(producto_id, creado_en)`. **Regla:** stock solo cambia insertando aquí (en la misma transacción que actualiza `inventario`).

### Ventas

**ventas**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| consecutivo | BIGINT | NOT NULL (de SEQUENCE), UNIQUE |
| cliente_id | BIGINT | FK clientes(id), NULL |
| vendedor_id | BIGINT | FK usuarios(id), NOT NULL |
| fecha | TIMESTAMPTZ | NOT NULL, DEFAULT now() |
| subtotal | NUMERIC(12,2) | NOT NULL |
| impuestos | NUMERIC(12,2) | NOT NULL |
| total | NUMERIC(12,2) | NOT NULL |
| metodo_pago | metodo_pago | NOT NULL |
| estado | venta_estado | NOT NULL, DEFAULT 'completada' |
| origen | venta_origen | NOT NULL, DEFAULT 'web' |
| idempotency_key | TEXT | UNIQUE (clave para reintentos/offline) |

Índices: `UNIQUE(consecutivo)`, `UNIQUE(idempotency_key)`, `(fecha)`, `(vendedor_id, fecha)`.

**ventas_detalle**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| venta_id | BIGINT | FK ventas(id) ON DELETE CASCADE, NOT NULL |
| producto_id | BIGINT | FK productos(id), NULL (null = venta varia) |
| descripcion | TEXT | para ítems sin código |
| cantidad | NUMERIC(12,3) | NOT NULL |
| precio_unitario | NUMERIC(12,2) | NOT NULL |
| iva | SMALLINT | NOT NULL |

**historico_ventas** (rollup diario para reportes/balances; NO se deriva en la migración)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| fecha | DATE | PK |
| ventas | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| efectivo | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| transferencia | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| datafono | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| n_transacciones | INTEGER | NOT NULL, DEFAULT 0 |
| gastos | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| abonos_proveedores | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| origen | TEXT | NOT NULL, DEFAULT 'calculado' |
| incluir_en_balances | BOOLEAN | NOT NULL, DEFAULT true |
| notas | TEXT | |
| actualizado_en | TIMESTAMPTZ | |

### Compras

**compras**: id PK, proveedor_id FK, fecha, total NUMERIC(12,2), creado_en.
**compras_detalle**: id PK, compra_id FK ON DELETE CASCADE, producto_id FK, cantidad NUMERIC(12,3), costo NUMERIC(12,2). (Genera ENTRADA de inventario.)
**compras_fiscal**: id PK, compra_id FK, proveedor_nit, base, iva, total, soporte_url, creado_en. Eventos RADIAN: `cufe_proveedor`, `evento_030_at`…`evento_033_at`, `evento_estado` (pendiente/aceptada), `evento_error`. (Ver `facturacion-matias-extract.md` §14.)

### Cuentas por pagar (proveedores) — v1

**facturas_proveedores** (deuda a proveedor)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | TEXT | PK (id de la factura del proveedor) |
| proveedor | TEXT | NOT NULL |
| descripcion | TEXT | |
| total | NUMERIC(12,2) | NOT NULL |
| pagado | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |
| pendiente | NUMERIC(12,2) | NOT NULL |
| estado | TEXT | NOT NULL, DEFAULT 'pendiente' |
| fecha | DATE | NOT NULL |
| foto_url / foto_nombre | TEXT | soporte (Cloudinary) |
| usuario_id | BIGINT | FK usuarios(id) |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

**facturas_abonos** (abonos a una factura de proveedor)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| factura_id | TEXT | FK facturas_proveedores(id) ON DELETE CASCADE |
| monto | NUMERIC(12,2) | NOT NULL |
| fecha | DATE | NOT NULL |
| foto_url / foto_nombre | TEXT | soporte del pago |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

> El `pendiente` se recalcula `total − Σ abonos`; `estado` pasa a `pagada` cuando llega a 0. Gobernado por la feature `compras_fiscal`/cuentas por pagar.

### Conciliación bancaria (Gmail) — v1

**bancolombia_transferencias** (transferencias detectadas por correo)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| gmail_message_id | TEXT | NOT NULL, UNIQUE (idempotencia de ingesta) |
| fecha | DATE | NOT NULL |
| hora | TEXT | |
| monto | NUMERIC(12,2) | NOT NULL |
| remitente | TEXT | |
| descripcion | TEXT | |
| tipo_transaccion | TEXT | |
| referencia | TEXT | |
| notificado | BOOLEAN | NOT NULL, DEFAULT true |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

### Caja y gastos

**caja**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| usuario_id | BIGINT | FK usuarios(id) |
| fecha_apertura | TIMESTAMPTZ | NOT NULL |
| saldo_inicial | NUMERIC(12,2) | NOT NULL |
| fecha_cierre | TIMESTAMPTZ | NULL |
| saldo_esperado | NUMERIC(12,2) | NULL |
| saldo_contado | NUMERIC(12,2) | NULL |
| diferencia | NUMERIC(12,2) | NULL |
| estado | caja_estado | NOT NULL, DEFAULT 'abierta' |

Índice parcial: una sola caja `abierta` por vendedor (`UNIQUE(usuario_id) WHERE estado='abierta'`).

**caja_movimientos**: id PK, caja_id FK, tipo caja_mov_tipo, monto NUMERIC(12,2), concepto TEXT, referencia TEXT, creado_en.
**gastos**: id PK, categoria gasto_categoria, monto NUMERIC(12,2), concepto TEXT, caja_id FK, usuario_id FK, creado_en. (Todo gasto inserta un egreso en caja_movimientos.)

### Clientes y proveedores

**clientes**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| nombre | TEXT | NOT NULL |
| tipo_documento | TEXT | CC, NIT, CE |
| documento | TEXT | índice |
| telefono | TEXT | |
| correo | TEXT | |
| direccion | TEXT | |
| ciudad_dane | TEXT | código DANE (para FE) |
| regimen | TEXT | régimen fiscal |
| saldo_fiado | NUMERIC(12,2) | NOT NULL, DEFAULT 0 |

**proveedores**: id PK, nombre, nit, telefono, correo, creado_en.

### Fiados y honorarios

**fiados**: id PK, cliente_id FK NOT NULL, venta_id FK NULL, monto NUMERIC(12,2), saldo NUMERIC(12,2), creado_en.
**fiados_movimientos**: id PK, fiado_id FK, tipo fiado_mov_tipo, monto NUMERIC(12,2), creado_en. (El saldo del cliente se recalcula con estos.)
**cuentas_cobro** (honorarios): id PK, consecutivo, numero_display, periodo, concepto, valor NUMERIC(12,2), `cliente_id` FK **NULL** (en FerreBot la cuenta de cobro es del operador, sin cliente), enviado_telegram BOOLEAN, creado_en. El PDF se guarda en Cloudinary (URL), no como `bytea`.

### Facturación DIAN

**facturas_electronicas**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| venta_id | BIGINT | FK ventas(id), NULL para DS |
| tipo | fe_tipo | NOT NULL |
| prefijo | TEXT | |
| consecutivo | BIGINT | de SEQUENCE propia por tipo |
| cufe | TEXT | NULL hasta emitir |
| estado | fe_estado | NOT NULL, DEFAULT 'pendiente' |
| xml_url | TEXT | |
| pdf_url | TEXT | |
| dian_respuesta | JSONB | última respuesta de MATIAS/DIAN |
| idempotency_key | TEXT | UNIQUE |
| intentos | SMALLINT | NOT NULL, DEFAULT 0 |
| creado_en / emitido_en | TIMESTAMPTZ | |

**notas_electronicas**: id PK, factura_id FK, tipo (nota_credito/nota_debito), motivo, cufe, estado fe_estado, creado_en.

**documentos_soporte** (DS-NO — tabla aparte; usa **CUDE**, resolución y consecutivo propios. Ver `facturacion-matias-extract.md` §13)
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| consecutivo | TEXT | de SEQUENCE propia de DS |
| fecha | DATE | |
| valor | NUMERIC(12,2) | |
| cude | TEXT | NULL hasta transmitir |
| estado_dian | TEXT | transmitido/rechazado_matias/rechazado_dian/error_conexion |
| cuenta_cobro_id | BIGINT | FK cuentas_cobro(id) |
| idempotency_key | TEXT | UNIQUE |
| intentos | SMALLINT | NOT NULL, DEFAULT 0 |
| creado_en / emitido_en | TIMESTAMPTZ | |
**eventos_dian**: id PK, factura_id FK, evento TEXT, estado, payload JSONB, creado_en.
**iva_saldos_bimestrales**: id PK, anio, bimestre, iva_generado, iva_descontable, saldo. UNIQUE(anio, bimestre).
**libro_iva**: vista/tabla de soporte tributario.

### Usuarios, config e IA

**usuarios**
| Columna | Tipo | Restricciones / nota |
|---|---|---|
| id | BIGSERIAL | PK |
| telegram_id | BIGINT | UNIQUE, NULL |
| nombre | TEXT | NOT NULL |
| rol | usuario_rol | NOT NULL, DEFAULT 'vendedor' |
| activo | BOOLEAN | NOT NULL, DEFAULT true |
| creado_en | TIMESTAMPTZ | DEFAULT now() |

**config_empresa**: vive en el **control DB**, no en la app DB (ver sección Control DB). Aquí no se crea.
**conversaciones_bot**: id PK, chat_id, rol (user/assistant), contenido, creado_en; índice (chat_id, creado_en).
**memoria_entidades**: id PK, tipo, clave, valor JSONB, actualizado_en.
**ventas_pendientes_voz**: id PK, chat_id, payload JSONB, estado, creado_en.
**audio_logs**: id PK, chat_id, transcripcion, duracion, creado_en.
**api_costo_diario**: fecha DATE PK, modelo, tokens_in, tokens_out, costo NUMERIC(12,4).

---

## Notas de implementación

- Cada app DB define sus `SEQUENCE` de consecutivos (ventas, factura, DS) para evitar carreras.
- Totales de venta = suma de detalle; el backend los calcula (nunca el cliente ni la IA).
- `idempotency_key` se genera en el cliente (web/bot/offline) y se valida en el backend antes de insertar.
- Migraciones: este esquema vive en `migrations/tenant/`; el control DB en `migrations/control/`.
