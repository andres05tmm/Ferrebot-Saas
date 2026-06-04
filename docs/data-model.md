# Modelo de datos

> Mapa conceptual. **Esquema físico detallado** (columnas, tipos, FKs, índices) en `schema.md`.

## Control DB (plano de control)

| Tabla | Campos clave |
|---|---|
| empresas | id, nombre, nit, slug/subdominio, estado, plan_id |
| tenant_databases | empresa_id, connection_url / instancia |
| planes | id, nombre, limites (json) |
| suscripciones | empresa_id, plan_id, estado (activa/suspendida/vencida), periodo |
| secretos_empresa | empresa_id, clave, valor_cifrado |
| branding | empresa_id, logo_url, color, nombre_comercial, dominio |
| super_admins | id, email, ... |

## App DB por empresa (esquema de negocio, = FerreBot)

Sin columna de empresa (la base es la frontera).

| Dominio | Tablas |
|---|---|
| Catálogo/inventario | productos, inventario, movimientos_inventario, productos_iva |
| Ventas | ventas, ventas_detalle, historico |
| Compras | compras, compras_detalle, compras_fiscal |
| Caja/gastos | caja (apertura/movimientos/cierre), gastos |
| Clientes/proveedores | clientes, proveedores, facturas_proveedores |
| Fiados/honorarios | fiados, fiados_movimientos, cuentas_cobro |
| Facturación DIAN | facturas_electronicas, documentos_soporte, notas_electronicas, eventos_dian |
| IVA | iva_saldos_bimestrales, libro_iva |
| IA/bot | conversaciones_bot, memoria_entidades, ventas_pendientes_voz, audio_logs, api_costo_diario |
| Usuarios/config | usuarios, config_empresa |

Reglas: stock solo cambia con movimiento de inventario; caja solo con movimiento de caja.
