import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabTopProductos from './TabTopProductos.jsx'

const TOP = [
  { producto_id: 1, nombre: 'Cemento', cantidad: '3', ingreso: '30000' },
  { producto_id: 2, nombre: 'Arena', cantidad: '4', ingreso: '20000' },
]

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/reportes/top-productos')) return Promise.resolve(jsonResp(TOP))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabTopProductos', () => {
  it('pide /reportes/top-productos (rango del mes) y pinta el ranking', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabTopProductos /></MemoryRouter>)

    expect(await screen.findByText('Cemento')).toBeInTheDocument()
    expect(screen.getByText('Arena')).toBeInTheDocument()
    expect(screen.getByText('$30.000')).toBeInTheDocument()   // ingreso del top 1

    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/reportes/top-productos'))
    expect(String(call[0])).toContain('desde=')
    expect(String(call[0])).toContain('hasta=')
    expect(String(call[0])).toContain('limite=10')
  })
})
