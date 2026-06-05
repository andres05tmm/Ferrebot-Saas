import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí capturamos el handler para disparar el re-fetch.
let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))

import TabHoy from './TabHoy.jsx'

const RESUMEN = { fecha: '2026-06-05', num_ventas: 3, total_vendido: '30000.00', ticket_promedio: '10000.00', por_metodo_pago: { efectivo: '30000.00' } }
const VENTAS = [{ id: 1, consecutivo: 5, fecha: '2026-06-05T15:00:00+00:00', total: '30000.00', metodo_pago: 'efectivo' }]
const STOCK = [{ producto_id: 2, nombre: 'Clavo', stock_actual: '3', stock_minimo: '10', bajo: true }]

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    if (String(url).includes('/inventario/stock')) return Promise.resolve(jsonResp(STOCK))
    if (String(url).includes('/ventas')) return Promise.resolve(jsonResp(VENTAS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabHoy', () => {
  it('pinta KPIs, métodos, últimas ventas y stock bajo desde los endpoints SaaS', async () => {
    instalarFetch()
    render(<MemoryRouter><TabHoy /></MemoryRouter>)

    expect(await screen.findByText('$10.000')).toBeInTheDocument()   // ticket promedio
    expect(screen.getAllByText('efectivo').length).toBeGreaterThan(0) // método de pago (+ badge venta)
    expect(screen.getByText('N.º 5')).toBeInTheDocument()             // última venta
    expect(screen.getByText('Clavo')).toBeInTheDocument()            // stock bajo
  })

  it("un evento 'venta_registrada' dispara re-fetch", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabHoy /></MemoryRouter>)
    await screen.findByText('$10.000')

    const resumenCalls = () => fetchMock.mock.calls.filter(c => String(c[0]).includes('/reportes/resumen')).length
    const antes = resumenCalls()
    await act(async () => { rtHandler('venta_registrada', {}) })
    expect(resumenCalls()).toBeGreaterThan(antes)
  })
})
