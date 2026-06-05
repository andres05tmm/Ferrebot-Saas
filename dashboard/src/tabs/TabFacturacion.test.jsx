import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { isRouteEnabled } from '@/lib/features.jsx'
import TabFacturacion from './TabFacturacion.jsx'

const FACTURAS = [
  { id: 1, venta_id: 99, prefijo: 'FPR', consecutivo: 1, cufe: 'CUFE1234567890', estado: 'aceptada', creado_en: '2026-06-05T15:00:00+00:00' },
  { id: 2, venta_id: 98, prefijo: 'FPR', consecutivo: 2, cufe: null, estado: 'rechazada', creado_en: '2026-06-05T16:00:00+00:00' },
]
const VENTAS = [
  { id: 10, consecutivo: 5, total: '11900', metodo_pago: 'efectivo', fecha: '2026-06-05T17:00:00+00:00' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ postStatus = 201 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/facturas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 3, estado: 'pendiente' }, postStatus))
    if (u.match(/\/facturas\/\d+/)) return Promise.resolve(jsonResp({ id: 2, total: '11900.00', motivo: 'NIT inválido', estado: 'rechazada' }))
    if (u.includes('/facturas')) return Promise.resolve(jsonResp(FACTURAS))
    if (u.includes('/ventas')) return Promise.resolve(jsonResp(VENTAS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabFacturacion', () => {
  it('lista las facturas con su badge de estado', async () => {
    instalarFetch()
    render(<MemoryRouter><TabFacturacion /></MemoryRouter>)

    expect(await screen.findByText('FPR-1')).toBeInTheDocument()
    expect(screen.getByText('FPR-2')).toBeInTheDocument()
    expect(screen.getByText('aceptada')).toBeInTheDocument()   // badge de estado
    expect(screen.getByText('rechazada')).toBeInTheDocument()
  })

  it('emitir: abre confirmación y SOLO al confirmar postea /facturas con shape + Idempotency-Key', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabFacturacion /></MemoryRouter>)
    await screen.findByText('FPR-1')

    // Abre el diálogo de confirmación fuerte (no postea todavía).
    fireEvent.click(screen.getByText('Facturar'))
    expect(await screen.findByText(/IRREVERSIBLE/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'POST')).toBe(false)

    // Cancelar NO postea.
    fireEvent.click(screen.getByText('Cancelar'))
    await waitFor(() => expect(screen.queryByText(/IRREVERSIBLE/)).toBeNull())
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'POST')).toBe(false)

    // Confirmar SÍ postea, con el shape correcto + Idempotency-Key.
    fireEvent.click(screen.getByText('Facturar'))
    await screen.findByText(/IRREVERSIBLE/)
    fireEvent.click(screen.getByText('Sí, emitir factura'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/facturas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ venta_id: 10 })
      // api.js normaliza los headers a un objeto Headers → leer con .get().
      expect(call[1].headers.get('Idempotency-Key')).toBeTruthy()
    })
  })

  it('el detalle (al expandir) trae el motivo de rechazo', async () => {
    instalarFetch()
    render(<MemoryRouter><TabFacturacion /></MemoryRouter>)
    await screen.findByText('FPR-2')

    fireEvent.click(screen.getByText('FPR-2'))                 // expande la rechazada
    expect(await screen.findByText(/NIT inválido/)).toBeInTheDocument()
  })

  it('el tab está gateado: la ruta no aparece sin la feature', () => {
    expect(isRouteEnabled('/facturacion', [])).toBe(false)
    expect(isRouteEnabled('/facturacion', ['facturacion_electronica'])).toBe(true)
  })
})
