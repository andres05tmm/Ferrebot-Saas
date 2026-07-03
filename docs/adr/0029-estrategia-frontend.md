# ADR 0029 — Estrategia de frontend: TanStack Query, TypeScript gradual y atajos de POS

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** plan de profesionalización 2026 (Fase 6, "Frontend base"), ADR 0014 (documento por venta)

## Contexto

El dashboard (`dashboard/`, React + Vite + Vitest) creció a 25+ pestañas sobre un hook de datos
casero (`useFetch` en `components/shared.jsx`): `fetch` vía `lib/api`, estado `loading/error/refetch`
a mano, sin caché compartida ni deduplicación entre componentes. Todo en JavaScript, sin tipos. El
POS (`TabVentasRapidas`) se opera con mouse, lento para un cajero con las manos en el teclado y sin
soporte para lector de código de barras.

La Fase 6 pone la **base** para profesionalizar el front sin reescribir lo que funciona: una capa de
datos moderna, tipos donde más valen, y ergonomía de teclado en el POS. El tiempo real por SSE
(`useRealtime`, `RealtimeProvider`) YA funciona y **no se toca**.

## Decisión

### D1 — TanStack Query como capa de datos, sin big-bang

Se adopta `@tanstack/react-query`. `QueryClientProvider` envuelve la app en la raíz (`main.jsx`), con
un `QueryClient` único (`lib/queryClient.ts`): `staleTime` 30s y `refetchOnWindowFocus:false` —el
dashboard ya recibe novedades por SSE, no necesita refetch agresivo—.

**Regla de convivencia (la norma de aquí en adelante):**

- Toda pantalla **nueva**, o aquella cuyo data-layer se **rehaga**, lee con `useQuery` y escribe con
  `useMutation` sobre `lib/api`. Las **mutaciones invalidan** las queries afectadas (por prefijo de
  clave; ver `lib/queries.ts`).
- `useFetch` (el hook casero) **sigue válido** en los 25+ tabs existentes. **No se migran** en bloque.
  Los dos enfoques conviven.
- El **SSE no cambia**: sigue empujando eventos y disparando refetch/invalidación donde ya lo hacía.
- Añadir un detalle a una pantalla (p. ej. un atajo de teclado) **no** obliga a migrar su fetching.

`lib/queries.ts` deja el patrón listo para copiar: `useProductos` (query con `enabled`) y
`useCrearProducto` (mutación que invalida el prefijo `['productos']`). Las claves viven en `queryKeys`.

### D2 — TypeScript gradual (`allowJs`), tipando primero `lib/api`

Se añade `tsconfig.json` con `allowJs:true` y `checkJs:false`: los `.jsx` existentes **no** se
type-checan; `strict:true` solo muerde a los `.ts/.tsx`. Vite/esbuild transpila sin `tsc`, así que la
config **no corre en `npm run build`**; el chequeo es opcional (`npm run typecheck` → `tsc --noEmit`,
verde sobre lo tipado).

`lib/api.js` pasa a `lib/api.ts` (primer archivo tipado): tipos `Producto`/`Cliente` y `apiJson<T>`
genérico. **No** se convierten los tabs a TS.

- **Resolución de imports:** este Vite **no** mapea un specifier `./api.js` a un fuente `./api.ts`.
  Para no reescribir 30+ imports con extensión falsa, los imports de los módulos que pasaron a `.ts`
  (`api`, `queries`, `schemas`, `queryClient`) quedan **sin extensión** (`@/lib/api`), y Vite los
  resuelve por `resolve.extensions` (incluye `.ts`). Los `.jsx` siguen importándose con su extensión
  real. `vite-env.d.ts` declara `import.meta.env` (incl. `VITE_TENANT_SLUG`) bajo `strict`.

### D3 — Validación de formularios con zod + react-hook-form (para formularios NUEVOS)

Se instalan `zod` y `react-hook-form`. El patrón queda listo en `lib/schemas.ts`: un schema zod por
formulario, el tipo del form por `z.infer`, y `zodResolver(schema)` a `useForm`. `zodResolver` es un
adaptador propio (no se añade `@hookform/resolvers`: una dependencia menos) válido para formularios
planos. Ejemplo/plantilla: `ventaVariaSchema` (descripción + cantidad + precio, con `coerce` de los
strings del `<input>` a número). Los formularios existentes **no** se migran.

### D4 — Atajos de teclado en el POS (`TabVentasRapidas`)

Un listener global en `document` (atado una vez; el estado vivo entra por refs) añade:

| Tecla | Acción |
|---|---|
| `F2` o `/` | Enfoca el buscador (`/` solo si no estás escribiendo en un campo, para no robar el carácter) |
| `Enter` (en el buscador, con resultados) | Agrega el primer resultado (top hit) |
| `F9` o `Ctrl`/`Cmd`+`Enter` | Cobrar (registrar venta) |
| `Alt`+`1..4` | Método de pago (efectivo · transferencia · datáfono · fiado) |
| Lector de código de barras | Ráfaga de teclas terminada en `Enter` → busca el código y agrega directo |

**Elección de teclas:** `F2`/`F9` (funciones libres en el navegador), `Alt`+dígito (no se teclea en
inputs ni choca con `Ctrl`+número de pestañas), y `/` restringido a fuera de campos. Todas hacen
`preventDefault` en su rama.

**Detección del lector (keyboard wedge):** las teclas imprimibles se acumulan en un buffer; una pausa
mayor a `IDLE_MS` (30 ms) lo descarta. El tecleo humano (pausas largas) resetea el buffer entre teclas
→ al `Enter` el buffer es corto y se trata como "agrega el primer resultado". El lector envía en
ráfaga (sin pausa) → el buffer supera `BARCODE_MIN` (4) y el `Enter` lo trata como escaneo:
`GET /productos?q=<código>`, match exacto por `codigo` (o el primero) y al carrito. Propiedad útil: si
un tecleo veloz se clasificara como escaneo, la búsqueda por ese texto agrega igual el producto
buscado — el fallo es benigno.

Un hint visual discreto sobre el buscador documenta los atajos en la propia UI.

## Consecuencias

- Base lista para que las pantallas nuevas usen datos cacheados/deduplicados y formularios validados,
  sin tocar los 25+ tabs ni el SSE. La migración es incremental y oportunista.
- El POS se opera sin mouse: buscar, agregar, elegir pago y cobrar con teclas, y escanear códigos de
  barras directo — ergonomía de caja real.
- `tsc --noEmit` da una red de seguridad opcional sobre lo tipado sin frenar el build de Vite.
- Deuda consciente: `useFetch` y `useQuery` conviven (dos formas de pedir datos) hasta que la migración
  oportunista los unifique; y `bundle` del dashboard supera 500 kB (aviso de Vite) — code-splitting
  queda para una fase posterior.
