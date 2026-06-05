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

const CAJA_ABIERTA = {
  id: 1, usuario_id: 1, estado: 'abierta', saldo_inicial: '50000.00',
  fecha_apertura: '2026-06-05T13:00:00+00:00', fecha_cierre: null,
  saldo_esperado: null, saldo_contado: null, diferencia: null,
}

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/caja/actual')) return Promise.resolve(jsonResp(CAJA_ABIERTA))
    if (String(url).includes('/caja/movimiento')) return Promise.resolve(jsonResp({ id: 7 }, 201))
    return Promise.resolve(jsonResp({}))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCaja', () => {
  it('carga /caja/actual y registra un movimiento (POST + Idempotency-Key)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja abierta')

    fireEvent.change(screen.getByLabelText('Monto'), { target: { value: '5000' } })
    fireEvent.click(screen.getByText('Registrar'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/caja/movimiento') && c[1]?.method === 'POST')).toBe(true)
    })
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/caja/movimiento'))
    expect(call[1].headers.get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(call[1].body)).toMatchObject({ tipo: 'ingreso', monto: 5000 })
  })

  it("un evento 'caja_movimiento' dispara re-fetch de /caja/actual", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCaja /></MemoryRouter>)
    await screen.findByText('Caja abierta')

    const actualCalls = () => fetchMock.mock.calls.filter(c => String(c[0]).includes('/caja/actual')).length
    const antes = actualCalls()
    await act(async () => { rtHandler('caja_movimiento') })
    expect(actualCalls()).toBeGreaterThan(antes)
  })
})
