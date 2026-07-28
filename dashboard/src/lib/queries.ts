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
  historialLineas: (clave: string) => ['historial', 'lineas', clave] as const,
  historialResumen: (clave: string) => ['historial', 'resumen', clave] as const,
  bancosMovimientos: (estado: string) => ['bancos', 'movimientos', estado] as const,
  bancosTotales: (rango: string) => ['bancos', 'totales', rango] as const,
  bancosRemitentes: (rango: string) => ['bancos', 'remitentes', rango] as const,
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
  kardex: ['kardex'] as const,
  postventaConfig: ['postventa', 'config'] as const,
  reservasHabitaciones: ['reservas', 'habitaciones'] as const,
  ventas: ['ventas'] as const,
  libros: ['libros'] as const,
  retencionesConfig: ['retenciones', 'config'] as const,
  historial: ['historial'] as const,
  bancosMovimientos: ['bancos', 'movimientos'] as const,
  bancosTotales: ['bancos', 'totales'] as const,
  // Conciliar, sugerir y descartar mueven la lista Y los totales (lo descartado sale de "del
  // negocio", lo conciliado deja de contar como "sin clasificar"): se invalida el módulo entero.
  bancos: ['bancos'] as const,
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
//
// Estas tres queries se salen del `refetchOnWindowFocus: false` global, y por una razón concreta:
// los eventos del dashboard viajan por `pg_notify`, que es fuego y olvido — no se reencolan. Si la
// transferencia entra mientras la ventana está en segundo plano o con el SSE caído, el aviso se
// pierde y la fila solo aparece al recargar. El resto del dashboard tolera eso; acá no, porque el
// dueño compara contra el mensaje que SÍ le llegó al Telegram y concluye que el tab va atrasado.
// Con el `staleTime` de 30s, volver a la ventana enseguida no dispara nada.
const AL_VOLVER_A_LA_VENTANA = { refetchOnWindowFocus: true } as const
export function useMovimientosBancarios(estado: string, incluirDescartados = false, cuenta = '') {
  return useQuery({
    queryKey: queryKeys.bancosMovimientos(`${estado}|${incluirDescartados ? 'd' : ''}|${cuenta}`),
    queryFn: () => {
      const q = new URLSearchParams()
      if (estado) q.set('estado', estado)
      if (incluirDescartados) q.set('incluir_descartados', 'true')
      // Vacío = todas las cuentas. Solo se manda cuando el dueño eligió una lente concreta.
      if (cuenta) q.set('cuenta', cuenta)
      const qs = q.toString()
      return apiJson<Fila[]>(`/bancos/movimientos${qs ? `?${qs}` : ''}`)
    },
    ...AL_VOLVER_A_LA_VENTANA,
  })
}

// Cuánta plata entró en el período (solo créditos). Va aparte de la lista: la lista está acotada a
// 200 movimientos y el total tiene que contar TODO el período, no lo que quepa en pantalla.
export function useTotalesBancarios(desde: string, hasta: string) {
  return useQuery({
    queryKey: queryKeys.bancosTotales(`${desde}|${hasta}`),
    queryFn: () => apiJson<Fila>(`/bancos/totales?desde=${desde}&hasta=${hasta}`),
    ...AL_VOLVER_A_LA_VENTANA,
  })
}

// Quién repite (GET /bancos/remitentes). `enabled` corta la llamada mientras la tabla está
// colapsada: es un reporte secundario, no tiene por qué pagarse al abrir el tab.
export function useRemitentesRecurrentes(desde: string, hasta: string, activo: boolean, cuenta = '') {
  return useQuery({
    queryKey: queryKeys.bancosRemitentes(`${desde}|${hasta}|${cuenta}`),
    queryFn: () => {
      const q = new URLSearchParams({ desde, hasta })
      if (cuenta) q.set('cuenta', cuenta)
      return apiJson<Fila[]>(`/bancos/remitentes?${q}`)
    },
    enabled: activo,
    ...AL_VOLVER_A_LA_VENTANA,
  })
}

export function useSugerirConciliacion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api('/bancos/sugerir', { method: 'POST' }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.bancos }) },
  })
}

export function useConciliar() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ movId, tipo, idInterno }: { movId: number; tipo: string; idInterno: number }) =>
      api(`/bancos/movimientos/${movId}/conciliar`, {
        method: 'POST', headers: jsonHeaders, body: JSON.stringify({ tipo, id_interno: idInterno }),
      }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.bancos }) },
  })
}

// "No es venta": la plata personal o de la casa que también entra a esas cuentas. No borra nada —
// sella la fila para sacarla del pendiente, y el mismo endpoint en DELETE lo deshace.
export function useDescartarMovimiento() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ movId, descartar }: { movId: number; descartar: boolean }) =>
      api(`/bancos/movimientos/${movId}/descarte`, { method: descartar ? 'POST' : 'DELETE' }),
    onSuccess: (res) => { if (res.ok) qc.invalidateQueries({ queryKey: keyPrefix.bancos }) },
  })
}


// ── Historial: el libro de ventas (ADR 0029; el tab se rehizo sobre TanStack Query) ─────────────
//
// `GET /ventas/historial` devuelve una fila por RENGLÓN vendido, ya con producto, cliente y vendedor
// resueltos. Se pagina por VENTA, así que `filas` siempre trae grupos completos.
export function useHistorialLineas(desde: string, hasta: string, limite = 100) {
  return useQuery({
    queryKey: queryKeys.historialLineas(`${desde}|${hasta}|${limite}`),
    queryFn: () => apiJson<Fila>(
      `/ventas/historial?desde=${desde}&hasta=${hasta}&limite=${limite}`),
  })
}

// KPIs del período. Va SEPARADO del feed a propósito: el feed está acotado a N ventas y el total
// tiene que cubrir todo el rango. Sumar lo que quepa en pantalla daría un número más chico que el
// real, y nadie lo notaría.
export function useHistorialResumen(desde: string, hasta: string) {
  return useQuery({
    queryKey: queryKeys.historialResumen(`${desde}|${hasta}`),
    queryFn: () => apiJson<Fila>(`/reportes/resumen?desde=${desde}&hasta=${hasta}`),
  })
}
