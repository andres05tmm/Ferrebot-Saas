import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
// Vendedor: NO debe ver controles de ajuste/edición.
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => false }) }))

import TabInventario from './TabInventario.jsx'

const PRODUCTOS = [
  { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null },
  { id: 2, nombre: 'Clavo', precio_venta: '100', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null },
]
const STOCK = [{ producto_id: 1, nombre: 'Martillo', stock_actual: '50', stock_minimo: '10', bajo: false }]

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/inventario/stock')) return Promise.resolve(jsonResp(STOCK))
    if (String(url).includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabInventario (solo lectura)', () => {
  it('lista productos y filtra con ?q', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)

    expect(await screen.findByText('Martillo')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('q=mar'))).toBe(true)
    })
  })

  it('un vendedor NO ve controles de ajuste/edición', async () => {
    instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    expect(screen.queryByTitle('Ajustar stock')).toBeNull()
    expect(screen.queryByText('Guardar')).toBeNull()
  })
})
