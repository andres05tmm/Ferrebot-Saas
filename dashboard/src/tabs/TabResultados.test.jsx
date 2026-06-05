import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabResultados from './TabResultados.jsx'

const RESULTADOS = {
  desde: '2026-06-01', hasta: '2026-06-05',
  ingresos: '100000.00', costo_ventas: '60000.00', utilidad_bruta: '40000.00',
  gastos: '15000.00', utilidad_neta: '25000.00',
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/reportes/resultados')) return Promise.resolve(jsonResp(RESULTADOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabResultados', () => {
  it('admin: pide /reportes/resultados (rango del mes) y pinta el P&L', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText('$100.000')).toBeInTheDocument()   // ingresos
    expect(screen.getByText('$60.000')).toBeInTheDocument()           // costo de ventas
    expect(screen.getByText('$25.000')).toBeInTheDocument()           // utilidad neta
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/reportes/resultados'))
    expect(String(call[0])).toContain('desde=')
    expect(String(call[0])).toContain('hasta=')
  })

  it('vendedor: NO ve el P&L ni pide el endpoint', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/reportes/resultados'))).toBe(false)
  })
})
