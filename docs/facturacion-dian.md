# Facturación electrónica DIAN (MATIAS)

> Emisión asíncrona y resiliente. Solo para empresas con la capacidad `facturacion_electronica` (ver `feature-flags.md`). Tablas en `schema.md`.

## Principio

- **La venta se registra siempre**, aunque la DIAN tarde o falle. La emisión está **desacoplada** de la venta (cola), nunca la bloquea.
- Idempotencia: cada documento lleva `idempotency_key`; nunca se emite dos veces.

## Máquina de estados (`fe_estado`)

```
pendiente ──(job emite)──▶ enviada ──(webhook/poll)──▶ aceptada
    │                          │
    │                          └──▶ rechazada   (motivo DIAN; permite corregir/nota)
    └──(error transitorio)──▶ error ──(reintento backoff)──▶ enviada
                                  └──(N intentos)──▶ dead-letter (alerta)
```

- `pendiente`: creada, consecutivo reservado, aún no enviada.
- `enviada`: aceptada por MATIAS, en proceso DIAN.
- `aceptada`: CUFE + PDF + XML disponibles.
- `rechazada`: DIAN la rechaza (se notifica; puede requerir nota o corrección).
- `error`/`dead-letter`: fallo técnico; reintentos con backoff exponencial; tras N intentos va a dead-letter con alerta.

## Jobs (ARQ)

- `emitir_documento(factura_id)`: toma `pendiente`, llama a MATIAS, pasa a `enviada` o `error`. Reintentos con backoff.
- `reconciliar_pendientes()`: job periódico que consulta el estado de las `enviada` que no recibieron webhook.
- `POST /webhooks/matias` (firmado): actualiza a `aceptada`/`rechazada` y emite evento SSE.

## Consecutivos y resolución

- **SEQUENCE por tipo y por empresa** (factura, documento soporte). El consecutivo se **reserva al crear** (`pendiente`), no al emitir, para mantener orden.
- Resolución, prefijo y rango DIAN viven en los secretos/config de la empresa (`MATIAS_RESOLUTION`, `PREFIX`, `NUM_DESDE`).
- **Documento Soporte (DS-NO):** resolución y consecutivo **propios** distintos a la factura (`MATIAS_RESOLUTION_DSNO`, `DS_NUM_DESDE`). Se usa para compras a no obligados a facturar.

## city_id de MATIAS ≠ código DANE

- MATIAS usa IDs internos de ciudad, no el código DANE. Mantener un **caché DANE → ID interno por empresa** (`_get_city_id`); cargarlo de `GET /cities` de MATIAS. Enviar el DANE directo causa "city_id no existe".

## Notas crédito/débito

- Referencian la factura original (`factura_id`) y su CUFE. Requieren `notas_electronicas` (que depende de `facturacion_electronica`).

## Facturas recibidas (compras)

- Captura de facturas de proveedor por **Gmail** (webhook) o foto (Cloudinary). Alimentan compras/compras fiscal si la empresa tiene esas capacidades.
