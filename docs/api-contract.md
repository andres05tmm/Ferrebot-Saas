# Contrato de API (v1)

> Catálogo de endpoints para el dashboard y el bot. Esquemas de datos en `schema.md`; resolución de empresa en `tenancy.md`.

## Convenciones

- **Base:** `/api/v1`. Versionado en la ruta.
- **Empresa (tenant):** se resuelve por subdominio (`empresa.BASE_DOMAIN`) o por el claim `tenant_id` del JWT. Toda ruta de negocio opera contra la base de esa empresa. Ver `tenancy.md`.
- **Auth:** `Authorization: Bearer <JWT>`. El JWT lleva `{ sub: usuario_id, tenant_id, rol }`. Rutas de plataforma usan el JWT de `super_admin`.
- **Roles:** `super_admin` (plataforma) > `admin` (empresa) > `vendedor`. Cada endpoint indica el rol mínimo.
- **Idempotencia:** en POST que mutan dinero/stock, header `Idempotency-Key: <uuid>`. Reintentos con la misma clave devuelven el resultado original (no duplican).
- **Errores:** JSON `{ "error": "codigo", "detail": "mensaje" }` con HTTP 4xx/5xx. Validación → 422.
- **Fechas:** ISO-8601; el backend interpreta y devuelve en hora Colombia.
- **Paginación:** listas grandes aceptan `?limit=&offset=`; listas pequeñas no paginan.
- **Tiempo real:** las mutaciones emiten eventos SSE (columna "Emite").

---

## Auth — `/api/v1/auth`

| Método | Ruta | Rol | Request | Response |
|---|---|---|---|---|
| POST | `/auth/telegram` | público | Telegram Login Widget payload | `{ token, usuario }` (JWT) |
| POST | `/auth/refresh` | usuario | `{ refresh_token }` | `{ token }` |
| GET | `/auth/me` | usuario | — | `{ usuario_id, tenant_id, rol, nombre }` |

---

## Catálogo — `/api/v1/productos`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| GET | `/productos` | vendedor | `?q=&categoria=&activo=` (q = fuzzy/FTS) | — |
| GET | `/productos/{id}` | vendedor | — | — |
| POST | `/productos` | admin | producto (ver schema) | `inventario_actualizado` |
| PATCH | `/productos/{id}` | admin | campos a cambiar | `inventario_actualizado` |
| GET | `/productos/{id}/precio` | vendedor | precios venta/mayorista | — |

## Inventario / kardex — `/api/v1/inventario`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| GET | `/inventario/stock` | vendedor | `?bajo=true` (stock < mínimo) | — |
| POST | `/inventario/ajuste` | admin | `{ producto_id, cantidad, motivo }` (AJUSTE) | `inventario_actualizado` |
| GET | `/inventario/kardex/{producto_id}` | vendedor | movimientos del producto | — |

## Ventas — `/api/v1/ventas`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| POST | `/ventas` | vendedor | venta + detalle; **Idempotency-Key**; valida stock | `venta_registrada` |
| GET | `/ventas` | vendedor | `?desde=&hasta=&vendedor_id=` (vendedor: solo las suyas) | — |
| GET | `/ventas/{id}` | vendedor | venta + detalle | — |
| POST | `/ventas/{id}/anular` | admin | `{ motivo }`; revierte stock | `venta_anulada`, `inventario_actualizado` |
| POST | `/ventas/sync` | vendedor | **lote offline**: array de ventas con Idempotency-Key (ver `tenancy.md`/offline) | `venta_registrada`×N |

## Caja — `/api/v1/caja`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| GET | `/caja/actual` | vendedor | caja abierta del vendedor | — |
| POST | `/caja/apertura` | vendedor | `{ saldo_inicial }` | `caja_abierta` |
| POST | `/caja/cierre` | vendedor | `{ saldo_contado }` → calcula diferencia | `caja_cerrada` |
| POST | `/caja/movimiento` | vendedor | `{ tipo, monto, concepto }` | `caja_movimiento` |

## Gastos — `/api/v1/gastos`

| Método | Ruta | Rol | Request | Emite |
|---|---|---|---|---|
| POST | `/gastos` | vendedor | `{ categoria, monto, concepto }` (mueve caja) | `gasto_registrado` |
| GET | `/gastos` | vendedor | `?desde=&hasta=` | — |

## Compras — `/api/v1/compras`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| POST | `/compras` | admin | compra + detalle (ENTRADA de inventario) | `compra_registrada`, `inventario_actualizado` |
| GET | `/compras` | admin | `?desde=&hasta=&proveedor_id=` | — |
| POST | `/compras/{id}/factura` | admin | foto (Cloudinary) o datos fiscales | — |

## Clientes / Proveedores / Fiados

| Método | Ruta | Rol | Request / Notas |
|---|---|---|---|
| GET/POST | `/clientes` | vendedor | listar (`?q=`) / crear |
| GET | `/clientes/{id}/historial` | vendedor | compras, facturas, saldo |
| GET/POST | `/proveedores` | admin | listar / crear |
| POST | `/fiados` | vendedor | `{ cliente_id, venta_id, monto }`; **Idempotency-Key**; emite `fiado_registrado` |
| POST | `/fiados/{id}/abono` | vendedor | `{ monto }` (recalcula saldo; sobre-abono → 422); **Idempotency-Key**; emite `fiado_abonado` |
| GET | `/fiados/deudas` | vendedor | clientes con saldo |

## Facturación DIAN — `/api/v1/facturacion`

| Método | Ruta | Rol | Request / Notas | Emite |
|---|---|---|---|---|
| POST | `/facturacion/emitir` | vendedor | `{ venta_id }`; **encola** emisión async; **Idempotency-Key** | `factura_pendiente` |
| GET | `/facturacion/{id}` | vendedor | estado, CUFE, PDF/XML | — |
| POST | `/facturacion/documento-soporte` | admin | DS-NO (resolución propia) | `factura_pendiente` |
| POST | `/facturacion/{id}/nota` | admin | `{ tipo: credito\|debito, motivo }` | — |
| POST | `/webhooks/matias` | público (firma) | callback de estado DIAN | `factura_aceptada`/`factura_rechazada` |
| GET | `/facturacion/recibidas` | admin | facturas de proveedor (Gmail) | — |

## Reportes — `/api/v1/reportes`

| Método | Ruta | Rol | Notas |
|---|---|---|---|
| GET | `/reportes/ventas` | vendedor | `?periodo=diario\|semanal\|mensual\|anual` |
| GET | `/reportes/resultados` | admin | estado de resultados |
| GET | `/reportes/top-productos` | vendedor | ranking |
| GET | `/reportes/libro-iva` | admin | soporte tributario |
| POST | `/reportes/excel` | admin | genera Excel por IA |

## IA del dashboard — `/api/v1/chat`

| Método | Ruta | Rol | Notas |
|---|---|---|---|
| POST | `/chat` | vendedor | `{ mensaje }` → respuesta (Haiku/Sonnet según complejidad) |
| POST | `/chat-stream` | vendedor | streaming SSE de la respuesta |

## Tiempo real — `/api/v1/events`

| Método | Ruta | Rol | Notas |
|---|---|---|---|
| GET | `/events` | usuario | **SSE** acotado a la empresa. Eventos: ver catálogo abajo |

**Catálogo de eventos SSE:** `venta_registrada`, `venta_anulada`, `caja_abierta`, `caja_cerrada`, `caja_movimiento`, `gasto_registrado`, `fiado_registrado`, `fiado_abonado`, `compra_registrada`, `inventario_actualizado`, `factura_pendiente`, `factura_aceptada`, `factura_rechazada`. Payload mínimo: `{ tipo, id, resumen }`.

## Plataforma (super_admin) — `/api/v1/admin`

| Método | Ruta | Rol | Notas |
|---|---|---|---|
| POST | `/admin/empresas` | super_admin | **aprovisiona** empresa (crea base, migra, siembra) — async |
| GET | `/admin/empresas` | super_admin | listar empresas y estado |
| PATCH | `/admin/empresas/{id}` | super_admin | estado suscripción (activa/suspendida) |
| PUT | `/admin/empresas/{id}/branding` | super_admin | logo, color, nombre, dominio |
| PUT | `/admin/empresas/{id}/secretos` | super_admin | cargar secretos cifrados (MATIAS, etc.) |

## Bot (Telegram) — `/tg/{empresa}`

| Método | Ruta | Notas |
|---|---|---|
| POST | `/tg/{slug}` | webhook del bot de esa empresa (token por empresa). Resuelve tenant por `{slug}` |

## Capacidades por empresa (feature flags)

Ver `feature-flags.md`. Los endpoints fiscales llevan el guard `require_feature(...)`; si la empresa no tiene la capacidad responden **404**.

| Método | Ruta | Rol | Notas |
|---|---|---|---|
| GET | `/config` | usuario | Arranque del dashboard: `{ features[], branding, usuario }` |
| PUT | `/admin/empresas/{id}/features` | super_admin | Activar/desactivar capacidades de una empresa |

Endpoints gated por `facturacion_electronica`: `/facturacion/*`. Por `libro_iva`: `/reportes/libro-iva`. Por `compras_fiscal`: `/compras/{id}/factura` fiscal. Por `honorarios`: cuentas de cobro.

## Salud — sin versión

| Método | Ruta | Notas |
|---|---|---|
| GET | `/health` | verifica control DB y dependencias |
| GET | `/ready` | listo para atender |
