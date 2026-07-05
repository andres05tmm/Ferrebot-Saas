import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabEstadosFinancieros from './TabEstadosFinancieros.jsx'

const COMPROBACION = {
  filas: [{ codigo: '1105', nombre: 'Caja', naturaleza: 'debito',
            debitos: '100000.00', creditos: '0.00', saldo: '100000.00' }],
  total_debitos: '100000.00', total_creditos: '100000.00', cuadra: true,
}
const RESULTADOS = {
  ingresos: [{ codigo: '4135', nombre: 'Comercio', valor: '80000.00' }], costos: [], gastos: [],
  total_ingresos: '80000.00', total_costos: '0.00', total_gastos: '0.00', utilidad: '80000.00',
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('balance-comprobacion')) return Promise.resolve(jsonResp(COMPROBACION))
    if (u.includes('estado-resultados')) return Promise.resolve(jsonResp(RESULTADOS))
    return Promise.resolve(jsonResp({}))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabEstadosFinancieros', () => {
  it('admin: pinta el balance de comprobación con su cuadre', async () => {
    instalarFetch()
    render(<MemoryRouter><TabEstadosFinancieros /></MemoryRouter>)
    expect(await screen.findByText('Caja')).toBeInTheDocument()
    expect(screen.getByText('Cuadra')).toBeInTheDocument()
  })

  it('cambia a estado de resultados y pide su endpoint', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabEstadosFinancieros /></MemoryRouter>)
    await screen.findByText('Caja')
    fireEvent.click(screen.getByRole('button', { name: /Resultados/i }))
    expect(await screen.findByText('Comercio')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('estado-resultados'))).toBe(true)
  })

  it('vendedor: no ve los estados ni pide endpoints', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabEstadosFinancieros /></MemoryRouter>)
    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/contabilidad/'))).toBe(false)
  })
})
