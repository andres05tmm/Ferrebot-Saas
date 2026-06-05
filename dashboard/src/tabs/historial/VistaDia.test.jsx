import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_t, handler) => { rtHandler = handler },
}))

import VistaDia from './VistaDia.jsx'

const VENTAS = [{ id: 1, consecutivo: 5, fecha: '2026-06-05T15:00:00+00:00', total: '23800.00', metodo_pago: 'efectivo', estado: 'completada' }]
const DETALLE = {
  id: 1, consecutivo: 5, cliente_id: null, vendedor_id: 5, fecha: '2026-06-05T15:00:00+00:00',
  subtotal: '20000.00', impuestos: '3800.00', total: '23800.00', metodo_pago: 'efectivo',
  estado: 'completada', origen: 'web', idempotency_key: null,
  lineas: [{ producto_id: 1, descripcion: 'Martillo', cantidad: '2', precio_unitario: '11900.00', iva: 19 }],
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (/\/ventas\/\d+/.test(String(url))) return Promise.resolve(jsonResp(DETALLE))   // detalle
    if (String(url).includes('/ventas')) return Promise.resolve(jsonResp(VENTAS))        // lista
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('VistaDia (historial)', () => {
  it('lista las ventas del rango y al expandir pide el detalle con sus líneas', async () => {
    instalarFetch()
    render(<MemoryRouter><VistaDia /></MemoryRouter>)

    fireEvent.click(await screen.findByText('N.º 5'))           // expandir la venta
    expect(await screen.findByText('Martillo')).toBeInTheDocument()  // línea del detalle
  })

  it("un evento 'venta_registrada' dispara re-fetch de la lista", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 5')

    const listaCalls = () => fetchMock.mock.calls.filter(c => /\/ventas\?/.test(String(c[0]))).length
    const antes = listaCalls()
    await act(async () => { rtHandler('venta_registrada') })
    expect(listaCalls()).toBeGreaterThan(antes)
  })
})
