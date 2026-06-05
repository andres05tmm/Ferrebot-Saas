import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabGastos from './TabGastos.jsx'

const GASTOS = [{ id: 1, categoria: 'transporte', monto: '5000.00', concepto: 'Taxi', caja_id: 1, usuario_id: 1, creado_en: '2026-06-05T14:00:00+00:00' }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts) => {
    if (String(url).includes('/gastos') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }, 201))
    if (String(url).includes('/gastos')) return Promise.resolve(jsonResp(GASTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabGastos', () => {
  it('lista gastos del día y registra uno (POST con shape + Idempotency-Key)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)
    expect(await screen.findByText('Taxi')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Monto'), { target: { value: '8000' } })
    fireEvent.click(screen.getByText('Registrar'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/gastos') && c[1]?.method === 'POST')).toBe(true)
    })
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/gastos') && c[1]?.method === 'POST')
    expect(call[1].headers.get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(call[1].body)).toMatchObject({ categoria: 'transporte', monto: 8000 })
  })

  it('la lista filtra por hoy (envía ?desde&hasta)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)
    await screen.findByText('Taxi')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/gastos?desde='))).toBe(true)
  })
})
