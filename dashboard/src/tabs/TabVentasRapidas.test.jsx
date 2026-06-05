import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import TabVentasRapidas from './TabVentasRapidas.jsx'

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/productos')) {
      return Promise.resolve(jsonResp([{ id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }]))
    }
    if (String(url).includes('/clientes')) return Promise.resolve(jsonResp([]))
    if (String(url).includes('/ventas')) return Promise.resolve(jsonResp({ id: 9, consecutivo: 1 }))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabVentasRapidas', () => {
  it('la búsqueda llama GET /productos?q', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await screen.findByText('Martillo')

    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=mar'))).toBe(true)
  })

  it('registrar hace POST /ventas con el shape VentaCrear + Idempotency-Key y limpia el carrito', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))      // agrega al carrito

    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')  // carrito limpio tras el éxito

    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')
    expect(call).toBeTruthy()
    expect(call[1].headers.get('Idempotency-Key')).toBeTruthy()
    const body = JSON.parse(call[1].body)
    expect(body).toMatchObject({ metodo_pago: 'efectivo', origen: 'web', lineas: [{ producto_id: 1, cantidad: 1 }] })
  })
})
