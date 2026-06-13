# ADR 0018 — Dos familias de dashboard: gating por vertical, no solo por `pos`

> Estado: **Aceptado** (13 jun 2026). Decide cómo separar la familia **ferretería/retail** de la familia
> **atención a cliente/servicios** en el dashboard, cuando un pack de servicio reusa el catálogo POS y
> arrastra `pos` por dependencia. Extiende el ADR 0008 (POS deja de ser núcleo).

## Contexto

El ADR 0008 sacó el POS del núcleo: las rutas de retail (`/ventas`, `/caja`, `/inventario`, `/compras`,
`/proveedores`, `/gastos`, reportes POS) se gatean por el flag `pos`, y la portada se resuelve con
`resolveHomePath` (`/hoy` para POS, `/inicio` para servicios). Funcionó mientras "tener `pos`" equivalía a
"ser ferretería".

Dejó de ser cierto. Varios packs de servicio **reusan el catálogo del POS** y por eso dependen de `pos`
(`core/tenancy/catalogo.py`):

- `pack_pedidos` (ADR 0016) → `requiere pos` (el menú ES el catálogo + inventario del POS).
- `pack_ventas` (ADR 0017) → `requiere pos` (cotiza catálogo y precios del POS).

Consecuencia: el **restaurante demo** (`pos` + `pack_pedidos`) hoy se ve **igual que la ferretería**:
`features.jsx` trata `pos` como "es retail", así que `resolveHomePath` lo manda a `/hoy` y `RUTA_FEATURE`
le abre todo el retail (Caja, Compras, Inventario, Kárdex…). Un restaurante no tiene caja de mostrador ni
kárdex de ferretería: ve un dashboard que no es el suyo. El flag `pos` quedó **sobrecargado** —significa a
la vez "soy retail" y "uso el catálogo de productos"— y esos dos sentidos ya divergen.

## Decisión

**Discriminar por FAMILIA de dashboard, no por el flag `pos`.** Hay dos familias mutuamente excluyentes en
la experiencia de navegación:

- **Ferretería / retail.** Tiene `pos` y **ningún** pack de atención a cliente. Ve el cockpit `/hoy`, el
  retail (ventas, caja, inventario, compras, proveedores, gastos) y los reportes POS-específicos
  (top-productos, kárdex, historial).
- **Atención a cliente / servicios.** Tiene algún pack de servicio. Su portada es la del agente
  (`/inicio` o `/pedidos`) y **no** ve el retail, **aunque arrastre `pos` por dependencia**.

### D1 — Discriminador de familia: `esAtencionCliente(features)`

Un helper en `dashboard/src/lib/features.jsx` devuelve `true` si el tenant activa **alguno** de los packs
de servicio que definen la familia: `pack_agenda`, `pack_pedidos`, `pack_reservas`. Es el único punto que
decide "esto es un agente de servicios, no una ferretería".

> Se eligen los packs **de servicio con portada propia** como discriminadores. `pack_faq`/`canal_whatsapp`
> son canales transversales (también los puede tener una ferretería con bot), así que **no** definen
> familia por sí solos.

### D2 — Portada por vertical: `resolveHomePath`

```
pack_pedidos              → /pedidos     (comandera del restaurante: su home operativa)
pack_agenda | pack_reservas → /inicio    (home del agente: citas, pendientes, KPIs)
pos (y nada de lo anterior) → /hoy       (cockpit POS de ferretería, intacto)
resto                     → /inicio      (núcleo de servicio)
```

El orden importa: `pack_pedidos` gana sobre `pos` aunque el tenant tenga ambos (el restaurante).

### D3 — Gating del retail con condición compuesta

Las rutas retail/contables (`/hoy`, `/ventas`, `/caja`, `/inventario`, `/compras`, `/proveedores`,
`/gastos`, `/top-productos`, `/kardex`, `/historial`) pasan a requerir **`pos` Y `NOT esAtencionCliente`**.
Se marcan en un set `RUTAS_RETAIL` y `isRouteEnabled` aplica la condición compuesta antes del lookup en
`RUTA_FEATURE` (que se conserva como referencia, sin romper el patrón actual).

Lo demás no cambia:

- Rutas de servicio (`/agenda`, `/pedidos`, `/conversaciones`, `/conocimiento`, `/cartera`) y núcleo
  (`/clientes`, `/resultados`) siguen igual.
- `/inicio` sigue siendo excluyente con `/hoy` vía `resolveHomePath`.
- Las rutas **fiscales** (`/facturacion`, etc.) no cambian: un tenant de servicios no tiene esas features,
  así que ya quedan ocultas por su propio flag.

## Alcance

Cambio **solo de frontend** (`dashboard/src/lib/features.jsx` + tests). No se toca el backend ni el
catálogo: las dependencias `pack_pedidos → pos` / `pack_ventas → pos` son correctas a nivel de datos (esos
packs sí leen el catálogo POS); lo que estaba mal era **inferir la familia de UI** desde `pos`.

## Consecuencias

**A favor:** cada vertical ve su propio dashboard sin un fork de código; el restaurante deja de parecer una
ferretería; el discriminador de familia es un único helper testeado; la portada por vertical es declarativa.

**En contra / costo:** `pos` deja de ser autosuficiente para gatear retail —hay que recordar la condición
compuesta `pos && !esAtencionCliente`. Un tenant **híbrido** real (ferretería que además agenda citas)
caería en la familia servicios y perdería el retail; no existe hoy y se resolvería con un flag explícito si
aparece.

**Lo que NO cambia:** el backend, el catálogo y sus dependencias; el aislamiento por tenant; las rutas
fiscales y los packs de servicio en sí.

## Alternativas consideradas

- **Quitar la dependencia `pack_pedidos → pos`** y duplicar un "catálogo de menú" propio. Rechazado: el
  menú **es** el catálogo POS (ADR 0016); duplicarlo rompe la fuente única y obliga a re-sincronizar stock.
- **Un flag nuevo `es_retail` en el catálogo.** Rechazado para v1: más superficie en backend para algo que
  el frontend puede derivar de los packs ya existentes. Se reconsiderará si surge un tenant híbrido.
- **Gatear retail por ausencia de `canal_whatsapp`.** Rechazado: una ferretería puede tener bot de
  WhatsApp; el canal no define la familia. La definen los packs con portada propia.
