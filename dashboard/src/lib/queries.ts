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
  // Tabs F7 (ADR 0029): claves completas por pantalla. Las mutaciones y el SSE invalidan por el
  // prefijo correspondiente en `keyPrefix` (más abajo).
  cobros: (estado: string) => ['cobros', estado] as const,
  cotizaciones: (estado: string) => ['cotizaciones', 'lista', estado] as const,
  cotizacionesConfig: ['cotizaciones', 'config'] as const,
  kardex: (productoId: number | null) => ['kardex', productoId] as const,
  postventaSatisfaccion: ['postventa', 'satisfaccion'] as const,
  postventaRespuestas: ['postventa', 'respuestas'] as const,
  postventaConfig: ['postventa', 'config'] as const,
  reservasHabitaciones: (checkin: string, noches: number) =>
    ['reservas', 'habitaciones', checkin, noches] as const,
  venta: (id: number | null) => ['ventas', id] as const,
  libroMayor: (desde: string, hasta: string) => ['libros', 'mayor', desde, hasta] as const,
  libroAuxiliar: (desde: string, hasta: string) => ['libros', 'auxiliar', desde, hasta] as const,
  retencionesConfig: ['retenciones', 'config'] as const,
  bancosMovimientos: (estado: string) => ['bancos', 'movimientos', estado] as const,
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

// Prefijos para invalidar por familia desde las mutaciones y desde el SSE (coinciden por prefijo con
// las claves de arriba). Mantiene el patrón "una mutación invalida su familia".
export const keyPrefix = {
  cobros: ['cobros'] as const,
  cotizacionesLista: ['cotizaciones', 'lista'] as const,
  cotizacionesConfig: ['cotizaciones', 'config'] as const,
  kardex: ['kardex'] as const,
  postventaConfig: ['postventa', 'config'] as const,
  reservasHabitaciones: ['reservas', 'habitaciones'] as const,
  ventas: ['ventas'] as const,
  libros: ['libros'] as const,
  retencionesConfig: ['retenciones', 'config'] as const,
  bancosMovimientos: ['bancos', 'movimientos'] as const,
}

// JSON de list endpoints sin validar en runtime (los .jsx no se type-checan; el shape lo usa cada tab).
type Fila = Record<string, unknown>

const jsonHeaders = { 'Content-Type': 'application/json' } as const

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

// ── Cobros Bold (ADR 0013) ─────────────────────────────────────────────────────────────────────
export function useCobros(estado: string) {
  return useQuery({
    queryKey: queryKeys.cobros(estado),
    queryFn: () => apiJson<Fila[]>(estado ? `/pagos/cobros?estado=${estado}` : '/pagos/cobros'),
  })
}

// pagado-manual / cancelar sobre un cobro pendiente. Devuelve la Response para que el tab ramifique
// por status (409/403); solo invalida la familia cuando el backend confirmó (res.ok).
export function useAccionCobro() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, tipo }: { id: number; tipo: 'pagar' | 'cancelar' }) =>
      api(`/pagos/cobros/${id}/${tipo === 'pagar' ? 'pagado-manual' : 'cancelar'}`, { method: 'POST' }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.cobros }) },
  })
}

// ── Cotizaciones por WhatsApp (ADR 0017) ────────────────────────────────────────────────────────
export function useCotizaciones(estado: string) {
  return useQuery({
    queryKey: queryKeys.cotizaciones(estado),
    queryFn: () => apiJson<Fila[]>(estado ? `/cotizaciones?estado=${estado}` : '/cotizaciones'),
  })
}

// La config solo la lee el admin (403 para staff): `enabled` corta la llamada sin rol.
export function useCotizacionesConfig(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.cotizacionesConfig,
    queryFn: () => apiJson<Fila>('/cotizaciones/config'),
    enabled,
  })
}

export function useMarcarCotizacion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, estado }: { id: number; estado: string }) =>
      api(`/cotizaciones/${id}/estado`, { method: 'PUT', headers: jsonHeaders, body: JSON.stringify({ estado }) }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.cotizacionesLista }) },
  })
}

export function useGuardarCotizacionesConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/cotizaciones/config', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify(body) }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.cotizacionesConfig }) },
  })
}

// ── Kárdex (movimientos de inventario por producto) ─────────────────────────────────────────────
// La búsqueda de producto reutiliza `useProductos` (mismo GET /productos?q&limite=20).
export function useKardex(productoId: number | null) {
  return useQuery({
    queryKey: queryKeys.kardex(productoId),
    queryFn: () => apiJson<Fila[]>(`/inventario/kardex/${productoId}?limite=200`),
    enabled: productoId != null,
  })
}

// ── Postventa (plan §2.6) — todo admin ──────────────────────────────────────────────────────────
export function usePostventaSatisfaccion() {
  return useQuery({ queryKey: queryKeys.postventaSatisfaccion, queryFn: () => apiJson<Fila>('/postventa/satisfaccion') })
}

export function usePostventaRespuestas() {
  return useQuery({ queryKey: queryKeys.postventaRespuestas, queryFn: () => apiJson<Fila[]>('/postventa/respuestas') })
}

export function usePostventaConfig() {
  return useQuery({ queryKey: queryKeys.postventaConfig, queryFn: () => apiJson<Fila>('/postventa/config') })
}

export function useGuardarPostventaConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/postventa/config', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify(body) }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.postventaConfig }) },
  })
}

// ── Reservas por noches (pack_reservas) ─────────────────────────────────────────────────────────
export function useHabitaciones(busqueda: { checkin: string; noches: number } | null) {
  return useQuery({
    queryKey: queryKeys.reservasHabitaciones(busqueda?.checkin ?? '', busqueda?.noches ?? 0),
    queryFn: () =>
      apiJson<Fila[]>(`/reservas/habitaciones?checkin=${busqueda!.checkin}&noches=${busqueda!.noches}`),
    enabled: busqueda != null,
  })
}

// El backend es idempotente por recurso. Se invalida la disponibilidad tanto en éxito como en 409
// (la habitación dejó de estar libre): en ambos casos el listado cambió.
export function useCrearReserva() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/reservas', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) }),
    onSuccess: (res) => {
      if (res.ok || res.status === 409) qc.invalidateQueries({ queryKey: keyPrefix.reservasHabitaciones })
    },
  })
}

// ── Devoluciones con reintegro (ADR 0026) ───────────────────────────────────────────────────────
export function useVenta(id: number | null) {
  return useQuery({
    queryKey: queryKeys.venta(id),
    queryFn: () => apiJson<Fila>(`/ventas/${id}`),
    enabled: id != null,
  })
}

// Ventas con documento fiscal vivo (POS/FE) para emitir nota crédito. `q` busca por número O CUFE;
// vacío = las más recientes. La clave incluye `q` (debounced en el tab) para cachear por término.
export function useVentasFacturadas(q: string) {
  const term = q.trim()
  return useQuery({
    queryKey: ['ventas', 'facturadas', term],
    queryFn: () => apiJson<Fila[]>(`/devoluciones/ventas-facturadas${term ? `?q=${encodeURIComponent(term)}` : ''}`),
  })
}

// POST /devoluciones idempotente (Idempotency-Key por venta cargada). El tab lee el body de la
// Response para el toast (total/método) y ramifica por status (409/422/404).
export function useRegistrarDevolucion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ body, key }: { body: Record<string, unknown>; key: string }) =>
      api('/devoluciones', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify(body),
      }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.ventas }) },
  })
}

// ── Libros contables (ADR 0027) — solo lectura, solo admin ──────────────────────────────────────
// `refreshKey` (contador de refresco global del shell) va en la clave: cambiarlo fuerza un refetch,
// como hacía el dep [refreshKey] del useFetch original. El prefijo ['libros'] sigue casando para el SSE.
export function useLibroMayor(desde: string, hasta: string, enabled: boolean, refreshKey: number = 0) {
  return useQuery({
    queryKey: [...queryKeys.libroMayor(desde, hasta), refreshKey],
    queryFn: () => apiJson<Fila[]>(`/reportes/libro-mayor?desde=${desde}&hasta=${hasta}`),
    enabled,
  })
}

export function useLibroAuxiliar(desde: string, hasta: string, enabled: boolean, refreshKey: number = 0) {
  return useQuery({
    queryKey: [...queryKeys.libroAuxiliar(desde, hasta), refreshKey],
    queryFn: () => apiJson<Fila[]>(`/reportes/libro-auxiliar?desde=${desde}&hasta=${hasta}`),
    enabled,
  })
}

// ── Retenciones e INC (ADR 0027) — catálogo opt-in, solo admin ──────────────────────────────────
export function useRetencionesConfig() {
  return useQuery({ queryKey: queryKeys.retencionesConfig, queryFn: () => apiJson<Fila[]>('/retenciones/config') })
}

export function useGuardarRetencion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/retenciones/config', { method: 'PUT', headers: jsonHeaders, body: JSON.stringify(body) }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.retencionesConfig }) },
  })
}

// ── Conciliación bancaria (ADR 0028) — solo admin ───────────────────────────────────────────────
export function useMovimientosBancarios(estado: string) {
  return useQuery({
    queryKey: queryKeys.bancosMovimientos(estado),
    queryFn: () => apiJson<Fila[]>(estado ? `/bancos/movimientos?estado=${estado}` : '/bancos/movimientos'),
  })
}

export function useSugerirConciliacion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api('/bancos/sugerir', { method: 'POST' }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.bancosMovimientos }) },
  })
}

export function useConciliar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ movId, tipo, idInterno }: { movId: number; tipo: string; idInterno: number }) =>
      api(`/bancos/movimientos/${movId}/conciliar`, {
        method: 'POST', headers: jsonHeaders, body: JSON.stringify({ tipo, id_interno: idInterno }),
      }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.bancosMovimientos }) },
  })
}
