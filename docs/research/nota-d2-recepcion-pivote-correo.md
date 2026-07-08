# Nota — D2 (recepción de facturas de proveedor): pivote a correo. ESTACIONADO

> Hallazgo + decisión de ruta para D2, para retomar más adelante. **No se ejecuta ahora.**
> Relacionado: `docs/adr/0020-recepcion-facturas-proveedor-qr-radian.md` (estado: propuesto).

## Hallazgo (soporte MATIAS, jun 2026)

El **buzón de recepción automático** de MATIAS está **en desarrollo**. Por lo tanto, hoy **no se puede recibir ni jalar las facturas electrónicas de los proveedores vía MATIAS**. Esto:

- Responde la pregunta abierta #4 del ADR 0020 (buzón automático): **no disponible**.
- Probablemente también bloquea la #1 (que `import-track-id` traiga un documento *recibido*): seguramente depende del mismo buzón que aún no existe.

Conclusión: la ruta del ADR 0020 tal como está ("QR → CUFE → `import-track-id` → MATIAS trae el documento") **está bloqueada** por la falta del buzón.

## Decisión de ruta (acordada, a ejecutar luego): pivotar la ingestión al CORREO

La factura del proveedor llega igual por **email**: en Colombia el emisor está obligado a enviar el documento (XML oficial + PDF) por correo al receptor. Y la arquitectura ya contempla **"factura de compra por correo (Gmail)"** (`architecture.md` §10 y §12, integración Gmail/compras por empresa).

Ruta que **no depende del buzón de MATIAS**:

- **Ingestión = el XML que el proveedor manda por correo** → captarlo con Gmail → parsear el UBL 2.1 nosotros → autollenar la cuenta por pagar (`facturas_proveedores` con `fecha_vencimiento` real) + soporte fiscal (`compras_fiscal` + XML).
- **MATIAS solo para el acuse/aceptación RADIAN** (030/032/033) — ya construido (`RadianService`, Slice 6b).
- **El QR pasa a ser opcional** (era para obtener el CUFE; si ya se tiene el XML del correo, no hace falta para autollenar). El inventario sigue manual hasta una fase asistida (mapeo de líneas a catálogo — el problema duro, ya identificado en el ADR 0020).

## Pendiente por confirmar antes de ejecutar

1. ¿Los proveedores mandan la FE **por correo con el XML adjunto** (lo estándar), y la integración Gmail de compras ya lo capta o llega a un buzón conectable?
2. ¿`import-track-id` sirve para un **CUFE específico** sin depender del buzón? (secundario; el camino del correo no lo necesita).

## Implicación para el ADR 0020

Cuando se retome, **revisar/actualizar el ADR 0020**: cambiar la fuente de ingestión primaria de "pull de MATIAS por CUFE" a "XML del correo (Gmail)", dejando MATIAS solo para los eventos RADIAN. Con eso D2 deja de estar bloqueado por el buzón.

## Estado

**Estacionado.** Retomar cuando se priorice D2 (o cuando MATIAS libere el buzón, lo que llegue primero).
