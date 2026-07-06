import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_t, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabCaja from './TabCaja.jsx'

const ARQUEO_ABIERTA = {
  estado: 'abierta', caja_id: 1, fecha_apertura: '2026-06-05T13:00:00+00:00',
  saldo_inicial: '50000', ventas_efectivo: '30000', ingresos: '0', egresos: '8000',
  saldo_esperado: '72000',
}
const ARQUEO_CERRADA = {
  estado: 'cerrada', caja_id: null, fecha_apertura: null, saldo_inicial: '0',
  ventas_efectivo: '0', ingresos: '0', egresos: '0', saldo_esperado: '0',
}
const RESUMEN = { fecha: '2026-06-05', num_ventas: 3, total_vendido: '80000', ticket_promedio: '26666',
  por_metodo_pago: { efectivo: '30000', transferencia: '50000' } }
const GASTOS = [{ id: 1, categoria: 'transporte', monto: '8000', concepto: 'Gasolina', creado_en: '2026-06-05T16:00:00+00:00' }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ arqueo = ARQUEO_ABIERTA } = {}) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/caja/arqueo')) return Promise.resolve(jsonResp(arqueo))
    if (u.includes('/caja/movimiento')) return Promise.resolve(jsonResp({ id: 7 }, 201))
    if (u.includes('/caja/apertura')) return Promise.resolve(jsonResp({ id: 2, estado: 'abierta' }, 201))
    if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    if (u.includes('/gastos')) return Promise.resolve(jsonResp(GASTOS))
    return Promise.resolve(jsonResp({}))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCaja — caja abierta', () => {
  it('pinta KPIs, ingresos por método, cuadre y gastos del día', async () => {
    instalarFetch()
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja abierta')
    expect(screen.getAllByText('$72.000').length).toBeGreaterThan(0)   // efectivo esperado (KPI + cuadre)
    expect(screen.getByText('Efectivo')).toBeInTheDocument()       // ingreso por método
    expect(screen.getByText('Transferencia')).toBeInTheDocument()
    expect(screen.getByText('= Efectivo esperado')).toBeInTheDocument()   // cuadre
    expect(screen.getByText('Gasolina')).toBeInTheDocument()       // gasto del día
  })

  it('registra un movimiento (POST + Idempotency-Key)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja abierta')

    fireEvent.change(screen.getByLabelText('Monto'), { target: { value: '5000' } })
    fireEvent.click(screen.getByText('Registrar'))

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/caja/movimiento') && c[1]?.method === 'POST')).toBe(true))
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/caja/movimiento'))
    expect(new Headers(call[1].headers).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(call[1].body)).toMatchObject({ tipo: 'ingreso', monto: 5000 })
  })

  it("un evento 'caja_movimiento' dispara re-fetch del arqueo", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja abierta')

    const arqueoCalls = () => fetchMock.mock.calls.filter(c => String(c[0]).includes('/caja/arqueo')).length
    const antes = arqueoCalls()
    await act(async () => { rtHandler('caja_movimiento') })
    expect(arqueoCalls()).toBeGreaterThan(antes)
  })
})

describe('TabCaja — caja cerrada', () => {
  it('muestra el formulario de apertura y abre la caja (POST /caja/apertura)', async () => {
    const fetchMock = instalarFetch({ arqueo: ARQUEO_CERRADA })
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja cerrada')

    fireEvent.change(screen.getByLabelText('Saldo inicial'), { target: { value: '100000' } })
    fireEvent.click(screen.getByRole('button', { name: /Abrir caja/i }))

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/caja/apertura') && c[1]?.method === 'POST')).toBe(true))
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/caja/apertura'))
    expect(JSON.parse(call[1].body)).toMatchObject({ saldo_inicial: 100000 })
  })
})
