/*
 * queries.ts — hooks de datos con TanStack Query (ADR 0029).
 *
 * Ejemplos del patrón (useQuery/useMutation sobre lib/api) listos para usar y para copiar en
 * pantallas nuevas. Las claves viven en `queryKeys` para que las mutaciones invaliden por prefijo.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, apiJson, type Producto } from './api'

export const queryKeys = {
  productos: (q: string) => ['productos', q] as const,
}

// Búsqueda de productos (GET /productos?q). `enabled` corta la query con q vacío (sin llamada).
export function useProductos(q: string) {
  return useQuery({
    queryKey: queryKeys.productos(q),
    queryFn: () => apiJson<Producto[]>(`/productos?q=${encodeURIComponent(q.trim())}&limite=20`),
    enabled: q.trim().length > 0,
  })
}

// Ejemplo de mutación con invalidación: al crear/editar un producto se invalidan TODAS las
// búsquedas de productos (prefijo ['productos']) para que reflejen el cambio.
export function useCrearProducto() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/productos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['productos'] }) },
  })
}
