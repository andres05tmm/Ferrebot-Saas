import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

// Rol controlable por test: admin ve los controles CRUD; vendedor sigue en solo-lectura.
const authState = vi.hoisted(() => ({ admin: false }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabInventario from './TabInventario.jsx'

const PRODUCTOS = [
  { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null, iva: 19 },
  { id: 2, nombre: 'Clavo', precio_venta: '100', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null, iva: 19 },
]
const STOCK = [{ producto_id: 1, nombre: 'Martillo', stock_actual: '50', stock_minimo: '10', bajo: false }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch(stock = STOCK) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/inventario/conteo') && opts?.method === 'POST')
      return Promise.resolve(jsonResp({ producto_id: 1, movimiento_id: 5, delta: '40', stock_actual: '40', replay: false }, 201))
    if (u.includes('/inventario/stock')) return Promise.resolve(jsonResp(stock))
    if (u.includes('/productos') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 99 }, 201))
    if (u.includes('/productos') && opts?.method === 'PUT') return Promise.resolve(jsonResp({ id: 1 }, 200))
    if (u.includes('/productos') && opts?.method === 'DELETE') return Promise.resolve(jsonResp({ producto_id: 1, activo: false }, 200))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = false })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabInventario — solo lectura (vendedor)', () => {
  it('lista productos (activo=true) y filtra con ?q', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)

    expect(await screen.findByText('Martillo')).toBeInTheDocument()
    // El listado por defecto pide solo activos.
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('activo=true'))).toBe(true)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('q=mar'))).toBe(true)
    })
  })

  it('un vendedor NO ve controles de crear/editar/eliminar/ajustar', async () => {
    instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    expect(screen.queryByText('Nuevo producto')).toBeNull()
    expect(screen.queryByTitle('Ajustar stock')).toBeNull()
    expect(screen.queryByTitle('Editar producto')).toBeNull()
    expect(screen.queryByTitle('Eliminar producto')).toBeNull()
  })
})

describe('TabInventario — CRUD (admin)', () => {
  beforeEach(() => { authState.admin = true })

  it('admin ve los controles de crear/editar/eliminar', async () => {
    instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    expect(screen.getByText('Nuevo producto')).toBeInTheDocument()
    expect(screen.getAllByTitle('Editar producto').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('Eliminar producto').length).toBeGreaterThan(0)
  })

  it('crear postea el shape ProductoCrear correcto (POST /productos)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Cemento' } })
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '50000' } })
    fireEvent.change(screen.getByLabelText('Stock inicial'), { target: { value: '10' } })
    fireEvent.click(screen.getByText('Crear producto'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')).toBe(true)
    })
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')
    expect(JSON.parse(call[1].body)).toMatchObject({
      nombre: 'Cemento', precio_venta: 50000, iva: 19, stock_inicial: 10,
      permite_fraccion: false, activo: true, fracciones: [],
    })
  })

  it('editar hace PUT /productos/{id}', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Editar producto')[0])
    expect(await screen.findByText('Editar producto')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '12500' } })
    fireEvent.click(screen.getByText('Guardar cambios'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/productos/1') && c[1]?.method === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({ precio_venta: 12500 })
    })
  })

  it('eliminar hace DELETE (soft) tras confirmar', async () => {
    const fetchMock = instalarFetch()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Eliminar producto')[0])
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos/1') && c[1]?.method === 'DELETE')).toBe(true)
    })
  })

  it('NO elimina si el usuario cancela la confirmación', async () => {
    const fetchMock = instalarFetch()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Eliminar producto')[0])
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false)
  })

  it('conteo físico postea /inventario/conteo con cantidad_contada (no el delta)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Ajustar stock')[0])           // abre el panel del producto 1
    fireEvent.change(await screen.findByLabelText('Cantidad real contada'), { target: { value: '40' } })
    fireEvent.click(screen.getByText('Ajustar a real'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/inventario/conteo') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({ producto_id: 1, cantidad_contada: 40 })
    })
  })
})

describe('TabInventario — indicador de stock negativo (suave)', () => {
  it('una fila con stock < 0 muestra "por cuadrar" con tooltip, sin estilo de error', async () => {
    instalarFetch([{ producto_id: 2, nombre: 'Clavo', stock_actual: '-5', stock_minimo: '0', bajo: false }])
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Clavo')

    const etiqueta = await screen.findByText('por cuadrar')
    expect(etiqueta).toBeInTheDocument()
    expect(etiqueta.className).not.toMatch(/destructive/)            // ámbar atenuado, NO rojo
    // Tooltip amable explicativo (sin ⚠️).
    expect(screen.getByTitle(/conteo físico para cuadrar/i)).toBeInTheDocument()
    expect(screen.queryByText('⚠️')).toBeNull()
  })
})
