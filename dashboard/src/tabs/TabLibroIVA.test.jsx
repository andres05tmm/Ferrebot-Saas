import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import { isRouteEnabled } from '@/lib/features.jsx'
import TabLibroIVA from './TabLibroIVA.jsx'

const LIBRO = {
  desde: '2026-06-01', hasta: '2026-06-05',
  base_ventas: '150000.00', iva_generado: '28500.00',
  base_compras: '100000.00', iva_descontable: '19000.00', saldo: '9500.00',
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch(libro = LIBRO) {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/reportes/libro-iva')) return Promise.resolve(jsonResp(libro))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabLibroIVA', () => {
  it('admin: pide /reportes/libro-iva (rango del mes) y pinta los totales + saldo', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabLibroIVA /></MemoryRouter>)

    expect(await screen.findByText('$28.500')).toBeInTheDocument()    // IVA generado
    expect(screen.getByText('$19.000')).toBeInTheDocument()           // IVA descontable
    expect(screen.getByText('$9.500')).toBeInTheDocument()            // saldo
    expect(screen.getByText('Saldo a pagar')).toBeInTheDocument()     // saldo positivo → a pagar
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/reportes/libro-iva'))
    expect(String(call[0])).toContain('desde=')
    expect(String(call[0])).toContain('hasta=')
  })

  it('saldo negativo se rotula como "a favor"', async () => {
    instalarFetch({ ...LIBRO, iva_generado: '5000.00', iva_descontable: '14500.00', saldo: '-9500.00' })
    render(<MemoryRouter><TabLibroIVA /></MemoryRouter>)

    expect(await screen.findByText('Saldo a favor')).toBeInTheDocument()
    expect(screen.getByText('$9.500')).toBeInTheDocument()            // |saldo| sin el signo
  })

  it('vendedor: NO ve el Libro IVA ni pide el endpoint', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabLibroIVA /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/reportes/libro-iva'))).toBe(false)
  })

  it('el tab está gateado: la ruta no aparece sin la feature', () => {
    expect(isRouteEnabled('/libro-iva', [])).toBe(false)
    expect(isRouteEnabled('/libro-iva', ['libro_iva'])).toBe(true)
  })
})
