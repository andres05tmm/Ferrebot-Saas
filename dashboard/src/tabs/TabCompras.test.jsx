import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabCompras from './TabCompras.jsx'

const COMPRAS = [
  { id: 1, proveedor_id: 1, proveedor_nombre: 'Ferre Mayorista', fecha: '2026-06-05T12:00:00+00:00', total: '80000.00' },
]
const PRODUCTOS = [{ id: 7, nombre: 'Cemento', precio_venta: '20000', unidad_medida: 'unidad', activo: true }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/compras') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2, total: '80000.00' }, 201))
    if (u.includes('/compras')) return Promise.resolve(jsonResp(COMPRAS))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCompras', () => {
  it('admin: registra una compra (POST /compras con el shape correcto) y ve la lista', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCompras /></MemoryRouter>)

    expect(await screen.findByText('Ferre Mayorista')).toBeInTheDocument()   // lista del rango

    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Distribuidora' } })

    // Buscar y elegir el producto.
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'cem' } })
    fireEvent.click(await screen.findByText('Cemento'))

    fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Costo unitario'), { target: { value: '8000' } })
    fireEvent.click(screen.getByText('Agregar item'))
    fireEvent.click(screen.getByText('Registrar compra'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/compras') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        proveedor: { nombre: 'Distribuidora', nit: null },
        items: [{ producto_id: 7, cantidad: 10, costo: 8000 }],
      })
    })
  })

  it('vendedor: no ve los controles de registro', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCompras /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(screen.queryByText('Registrar compra')).toBeNull()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/compras'))).toBe(false)
  })
})
