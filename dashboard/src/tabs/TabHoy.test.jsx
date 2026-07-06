import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí capturamos el handler (re-fetch) y la lista de eventos.
let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))
// FeedActividad también se suscribe a eventos; se stubbea para que la captura de rtEventos/rtHandler
// refleje SOLO la suscripción de TabHoy (el feed tiene su propio test).
vi.mock('@/components/FeedActividad.jsx', () => ({ default: () => null }))

import TabHoy from './TabHoy.jsx'

const RESUMEN = { fecha: '2026-06-05', num_ventas: 3, total_vendido: '30000.00', ticket_promedio: '10000.00', por_metodo_pago: { efectivo: '30000.00' } }
const TOTALES = { dia: '30000.00', semana: '120000.00', mes: '500000.00' }
const SERIE = [
  { fecha: '2026-05-30', total: '5000.00' }, { fecha: '2026-05-31', total: '7000.00' },
  { fecha: '2026-06-01', total: '0' }, { fecha: '2026-06-02', total: '12000.00' },
  { fecha: '2026-06-03', total: '8000.00' }, { fecha: '2026-06-04', total: '15000.00' },
  { fecha: '2026-06-05', total: '30000.00' },
]
const VENTAS = [{ id: 1, consecutivo: 5, fecha: '2026-06-05T15:00:00+00:00', total: '30000.00', metodo_pago: 'efectivo' }]
const STOCK = [{ producto_id: 2, nombre: 'Clavo', stock_actual: '3', stock_minimo: '10', bajo: true }]
const TOP = [{ producto_id: 7, nombre: 'Cemento', cantidad: '4', ingreso: '24000.00' }]
const GASTOS = [{ id: 1, categoria: 'transporte', monto: '5000.00', creado_en: '2026-06-05T16:00:00+00:00' }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    if (u.includes('/reportes/serie-ventas')) return Promise.resolve(jsonResp(SERIE))
    if (u.includes('/reportes/totales')) return Promise.resolve(jsonResp(TOTALES))
    if (u.includes('/reportes/top-productos')) return Promise.resolve(jsonResp(TOP))
    if (u.includes('/caja/actual')) return Promise.resolve(jsonResp({ detail: 'No hay caja abierta' }, 404)) // cerrada
    if (u.includes('/gastos')) return Promise.resolve(jsonResp(GASTOS))
    if (u.includes('/inventario/stock')) return Promise.resolve(jsonResp(STOCK))
    if (u.includes('/ventas')) return Promise.resolve(jsonResp(VENTAS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabHoy — paridad', () => {
  it('pinta KPIs, semana/mes, evolución, métodos, últimas ventas, top y stock bajo', async () => {
    instalarFetch()
    render(<MemoryRouter><TabHoy /></MemoryRouter>)

    expect(await screen.findByText('$10.000')).toBeInTheDocument()    // ticket promedio (resumen)
    expect(screen.getByText('$120.000')).toBeInTheDocument()          // total semana (totales)
    expect(screen.getByText('$500.000')).toBeInTheDocument()          // total mes (totales)
    expect(screen.getByText('Evolución de ventas')).toBeInTheDocument() // gráfica (serie-ventas)
    expect(screen.getAllByText('Efectivo').length).toBeGreaterThan(0)  // método de pago (capitalizado)
    expect(screen.getByText('N.º 5')).toBeInTheDocument()             // última venta
    expect(screen.getAllByText('Cemento').length).toBeGreaterThan(0)  // top productos (feed + panel)
    expect(screen.getByText('Clavo')).toBeInTheDocument()             // stock bajo
    expect(screen.getByText('Pendiente de apertura')).toBeInTheDocument() // caja 404 → cerrada (no rompe)
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

describe('TabHoy — estado fiscal', () => {
  const VENTA_FISCAL = {
    id: 2, consecutivo: 6, fecha: '2026-06-05T16:00:00+00:00', total: '50000.00', metodo_pago: 'efectivo',
    fiscal: { tipo: 'pos', estado: 'aceptada', cufe: 'CUDE-9', numero: 7, prefijo: 'DPOS' },
  }

  it('pinta el badge fiscal en las últimas ventas y se suscribe a los eventos fiscales', async () => {
    const fetchMock = vi.fn((url) => {
      const u = String(url)
      if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
      if (u.includes('/ventas')) return Promise.resolve(jsonResp([VENTA_FISCAL]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabHoy /></MemoryRouter>)

    const badge = await screen.findByText(/POS · aceptada/i)
    expect(badge).toHaveClass('text-success')                       // aceptada → variante verde
    expect(rtEventos).toEqual(expect.arrayContaining(['factura_aceptada', 'factura_rechazada', 'factura_anulada']))
  })

  it("un evento 'factura_aceptada' dispara re-fetch de las ventas", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabHoy /></MemoryRouter>)
    await screen.findByText('$10.000')

    const ventasCalls = () => fetchMock.mock.calls.filter(c => /\/ventas\b/.test(String(c[0]))).length
    const antes = ventasCalls()
    await act(async () => { rtHandler('factura_aceptada', {}) })
    expect(ventasCalls()).toBeGreaterThan(antes)
  })
})
