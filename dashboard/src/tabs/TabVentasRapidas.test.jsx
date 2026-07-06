import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/features.jsx', () => ({ useFeatures: () => [] }))

import TabVentasRapidas from './TabVentasRapidas.jsx'

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

const MARTILLO = { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }
const TALADRO_ESP = { id: 2, nombre: 'Taladro', precio_venta: '100000', precio_especial: '90000', unidad_medida: 'unidad' }

// PrecioLeer del motor: total ≠ precio_venta*cantidad para probar que manda el servidor.
function precioResp(id, cantidad) {
  return jsonResp({ producto_id: id, cantidad, precio_unitario: '10000', total: '10000', regla: 'escalonado' })
}

function instalarFetch(busqueda = [MARTILLO]) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
    if (/\/productos\/\d+\/precio/.test(u)) {
      const id = Number(u.match(/\/productos\/(\d+)\/precio/)[1])
      return Promise.resolve(precioResp(id, 1))
    }
    if (u.includes('/productos')) return Promise.resolve(jsonResp(busqueda))
    if (u.includes('/clientes')) return Promise.resolve(jsonResp([]))
    if (u.includes('/ventas')) return Promise.resolve(jsonResp({ id: 9, consecutivo: 1 }))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function ventaPost(fetchMock) {
  const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')
  expect(call).toBeTruthy()
  return { headers: call[1].headers, body: JSON.parse(call[1].body) }
}

async function agregarMartillo() {
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
  fireEvent.click(await screen.findByText('Martillo'))
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabVentasRapidas', () => {
  it('la búsqueda (con debounce) llama GET /productos?q', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await screen.findByText('Martillo')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=mar'))).toBe(true)
  })

  it('el total y el c/u vienen del servidor (GET /precio), no del precio_venta', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => /\/productos\/1\/precio/.test(String(c[0])))).toBe(true))
    // total del servidor ($10.000), no el precio_venta*cantidad ($11.900). Aparece en el total y el c/u.
    expect((await screen.findAllByText('$10.000')).length).toBeGreaterThan(0)
  })

  it('registrar hace POST /ventas SIN precio_unitario (server-authoritative) + Idempotency-Key', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)   // carrito limpio tras el éxito

    const { headers, body } = ventaPost(fetchMock)
    expect(headers.get('Idempotency-Key')).toBeTruthy()
    expect(body.lineas[0]).toEqual({ producto_id: 1, cantidad: 1 })   // sin precio_unitario
    expect(body.origen).toBe('web')
  })

  it('elegir "especial" envía precio_unitario como override explícito', async () => {
    const fetchMock = instalarFetch([TALADRO_ESP])
    render(<TabVentasRapidas />)
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'tal' } })
    fireEvent.click(await screen.findByText('Taladro'))
    fireEvent.click(await screen.findByText(/Especial/))
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)

    const { body } = ventaPost(fetchMock)
    expect(body.lineas[0].precio_unitario).toBe(90000)
  })
})
