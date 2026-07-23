import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabMesas from './TabMesas.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

const MESAS = [
  { id: 1, nombre: 'Mesa 1', zona: 'salón', activo: true, pedido_id: 7, total: '41000.00' },
  { id: 2, nombre: 'Mesa 2', zona: null, activo: true, pedido_id: null, total: null },
]
const PRECUENTA = {
  id: 7, cliente_nombre: 'Mesa 1', cliente_telefono: 'mesa:1', direccion: null, zona_id: null,
  costo_domicilio: '0.00', metodo_pago: null, estado: 'abierto', subtotal: '41000.00',
  total: '41000.00', notas: null, origen: 'mesa', creado_en: '2026-07-23T17:00:00+00:00',
  actualizado_en: '2026-07-23T17:00:00+00:00', venta_id: null,
  items: [
    { id: 1, producto_id: 9, nombre: 'Hamburguesa', cantidad: '2', precio_unitario: '18000.00',
      subtotal: '36000.00', modificadores: [{ grupo: 'Proteína', opcion: 'Carne', delta_precio: '0.00' }] },
    { id: 2, producto_id: 8, nombre: 'Limonada', cantidad: '1', precio_unitario: '5000.00', subtotal: '5000.00' },
  ],
}

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    const json = (data) => Promise.resolve({ ok: true, status: 200, json: async () => data })
    if (u.includes('/precuenta')) return json(PRECUENTA)
    if (u.includes('/cobrar')) return json({ venta_id: 3, total: '45000.00', replay: false })
    if (u.includes('/abrir')) return json(PRECUENTA)
    if (u.includes('/mesas')) return json(MESAS)
    return json([])
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabMesas', () => {
  it('la ruta /mesas se gatea por pack_mesas', () => {
    expect(isRouteEnabled('/mesas', ['pack_pedidos', 'ventas'])).toBe(false)
    expect(isRouteEnabled('/mesas', ['pack_mesas', 'ventas'])).toBe(true)
  })

  it('pinta la grilla con total en vivo y estado libre', async () => {
    instalarFetch()
    render(<MemoryRouter><TabMesas /></MemoryRouter>)
    expect(await screen.findByText('Mesa 1')).toBeInTheDocument()
    expect(screen.getByText('$41.000')).toBeInTheDocument()
    expect(screen.getByText('Libre')).toBeInTheDocument()
  })

  it('seleccionar una mesa abierta muestra la precuenta y cobra con propina', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabMesas /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Mesa Mesa 1' }))

    expect(await screen.findByText(/2× Hamburguesa/)).toBeInTheDocument()
    expect(screen.getByText('Carne')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Propina'), { target: { value: '4000' } })
    fireEvent.click(screen.getByRole('button', { name: /Cobrar/ }))
    await screen.findByText('Mesa 1')
    const llamada = fetchMock.mock.calls.find(c => /\/mesas\/1\/cobrar/.test(String(c[0])))
    expect(llamada[1].method).toBe('POST')
    expect(JSON.parse(llamada[1].body)).toEqual({ metodo_pago: 'efectivo', propina: '4000' })
  })

  it('mesa libre ofrece abrir', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabMesas /></MemoryRouter>)
    fireEvent.click(await screen.findByRole('button', { name: 'Mesa Mesa 2' }))
    fireEvent.click(await screen.findByRole('button', { name: /Abrir mesa/ }))
    await screen.findByText('Mesa 2')
    expect(fetchMock.mock.calls.some(c => /\/mesas\/2\/abrir/.test(String(c[0])))).toBe(true)
  })
})
