/*
 * queries.ts — hooks de datos con TanStack Query (ADR 0029).
 *
 * Ejemplos del patrón (useQuery/useMutation sobre lib/api) listos para usar y para copiar en
 * pantallas nuevas. Las claves viven en `queryKeys` para que las mutaciones invaliden por prefijo.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, apiJson, type Producto } from './api'

// Factura de proveedor recibida por QR (ADR 0020): soporte fiscal (CUFE + RADIAN) + cuenta por pagar.
// Los decimales llegan como string (COP sin float), igual que en el resto de la API.
export interface FacturaRecibida {
  cufe: string
  fiscal_id: number
  proveedor_nit: string | null
  base: string | number
  iva: string | number
  total: string | number
  evento_030_at: string | null
  evento_estado: string | null
  evento_error: string | null
  cuenta_por_pagar_id: string | null
  fecha: string | null
  fecha_vencimiento: string | null
  pendiente: string | number | null
  estado: string | null
  descripcion: string | null
}

export const queryKeys = {
  productos: (q: string) => ['productos', q] as const,
  facturasRecibidas: () => ['facturas-recibidas'] as const,
}

// Listado de facturas recibidas por QR (GET /facturas-recibidas). Gateado por `compras_fiscal` en el back.
export function useFacturasRecibidas() {
  return useQuery({
    queryKey: queryKeys.facturasRecibidas(),
    queryFn: () => apiJson<FacturaRecibida[]>('/facturas-recibidas'),
  })
}

// Escaneo/pegado del QR (POST /facturas-recibidas/escanear). Al resolver, invalida el listado.
// Lanza 'qr_invalido' en 422 (el QR no trae CUFE) para que la pantalla muestre un mensaje claro.
export function useEscanearQR() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (body: Record<string, unknown>): Promise<FacturaRecibida> => {
      const res = await api('/facturas-recibidas/escanear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (res.status === 422) throw new Error('qr_invalido')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return res.json() as Promise<FacturaRecibida>
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.facturasRecibidas() }) },
  })
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
