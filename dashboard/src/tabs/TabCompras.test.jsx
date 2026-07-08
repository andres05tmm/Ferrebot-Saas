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
import { FeaturesProvider } from '@/lib/features.jsx'

const COMPRAS = [
  { id: 1, proveedor_id: 1, proveedor_nombre: 'Ferre Mayorista', fecha: '2026-06-05T12:00:00+00:00', total: '80000.00' },
]
const PRODUCTOS = [{ id: 7, nombre: 'Cemento', precio_venta: '20000', unidad_medida: 'unidad', activo: true }]
const OBRAS = [
  { id: 5, nombre: 'Vía El Retiro', estado: 'activa' },
  { id: 8, nombre: 'Puente La Ceja', estado: 'activa' },
  { id: 9, nombre: 'Obra Vieja', estado: 'archivada' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ compras = COMPRAS } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/compras') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2, total: '80000.00' }, 201))
    if (u.includes('/compras')) return Promise.resolve(jsonResp(compras))
    if (u.includes('/obras')) return Promise.resolve(jsonResp(OBRAS))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderCon(features = null) {
  const tab = <TabCompras />
  return render(
    <MemoryRouter>
      {features ? <FeaturesProvider features={features}>{tab}</FeaturesProvider> : tab}
    </MemoryRouter>,
  )
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCompras', () => {
  it('admin: registra una compra (POST /compras con el shape correcto) y ve la lista', async () => {
    const fetchMock = instalarFetch()
    renderCon()

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
    renderCon()

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(screen.queryByText('Registrar compra')).toBeNull()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/compras'))).toBe(false)
  })
})

describe('TabCompras — modo obra / viaje (familia construcción)', () => {
  it('retail (pos+inventario): no muestra el toggle ni pide obras; el flujo catálogo sigue igual', async () => {
    const fetchMock = instalarFetch()
    renderCon(['pos', 'inventario'])

    await screen.findByText('Ferre Mayorista')
    // Sin el segmentado de tipo de compra ni el modo obra.
    expect(screen.queryByRole('group', { name: 'Tipo de compra' })).toBeNull()
    expect(screen.queryByText('Obra / viaje')).toBeNull()
    // El buscador de producto (catálogo) sigue presente.
    expect(screen.getByLabelText('Buscar producto')).toBeInTheDocument()
    // No se piden obras en retail.
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/obras'))).toBe(false)
  })

  it('construcción: aparece el toggle y el viaje postea es_viaje_material/precio/obra/categoria sin producto_id', async () => {
    const fetchMock = instalarFetch({ compras: [] })
    renderCon(['construccion'])

    // El segmentado de tipo de compra aparece en la familia construcción.
    const grupo = await screen.findByRole('group', { name: 'Tipo de compra' })
    expect(grupo).toBeInTheDocument()

    fireEvent.click(screen.getByText('Obra / viaje'))

    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Cantera del Río' } })

    // Selector de obra: solo obras vigentes (la archivada no aparece).
    await screen.findByRole('option', { name: 'Vía El Retiro' })
    expect(screen.queryByRole('option', { name: 'Obra Vieja' })).toBeNull()
    fireEvent.change(screen.getByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: 'MEZCLA_ASFALTICA' } })

    // Marca viaje → aparece el precio de venta y el preview del resbalo en vivo.
    fireEvent.click(screen.getByLabelText('Es viaje de material (se revende al cliente)'))
    fireEvent.change(screen.getByLabelText('Precio de venta al cliente'), { target: { value: '120000' } })

    // Item sin producto: solo cantidad + costo (costo del viaje = 3 × 30000 = 90000).
    fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Costo unitario'), { target: { value: '30000' } })
    fireEvent.click(screen.getByText('Agregar item'))

    // Resbalo = 120000 − 90000 = 30000 → 25% → margen sano.
    expect(await screen.findByText(/Margen sano/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('Registrar compra'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/compras') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      const body = JSON.parse(call[1].body)
      expect(body).toMatchObject({
        proveedor: { nombre: 'Cantera del Río', nit: null },
        obra_id: 5,
        categoria: 'MEZCLA_ASFALTICA',
        es_viaje_material: true,
        precio_venta_cliente: 120000,
        items: [{ cantidad: 3, costo: 30000 }],
      })
      // La línea de obra NO lleva producto de catálogo.
      expect(body.items[0].producto_id).toBeUndefined()
      // Idempotencia en la operación crítica (api() envuelve los headers en un Headers).
      expect(new Headers(call[1].headers).get('Idempotency-Key')).toBeTruthy()
    })
  })
})
