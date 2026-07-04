import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import TabDevoluciones from './TabDevoluciones.jsx'
import { conQuery } from '@/test/query.jsx'

const VENTA = {
  id: 5, consecutivo: 42, cliente_id: null, vendedor_id: 1, fecha: '2026-06-10T14:00:00+00:00',
  subtotal: '100000', impuestos: '0', total: '100000', metodo_pago: 'efectivo', estado: 'completada',
  origen: 'dashboard', idempotency_key: null,
  lineas: [
    { producto_id: 7, descripcion: 'Taladro', cantidad: '1', precio_unitario: '100000', iva: 0 },
  ],
}

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.endsWith('/devoluciones') && opts.method === 'POST') {
      return Promise.resolve(jsonResp({ id: 1, venta_id: 5, total: '100000', metodo_reintegro: 'efectivo', estado: 'ok' }, 201))
    }
    if (u.includes('/ventas/5')) return Promise.resolve(jsonResp(VENTA))
    return Promise.resolve(jsonResp({}, 404))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabDevoluciones', () => {
  it('parte pidiendo el número de venta', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    expect(screen.getByText(/Escribe el número de una venta/i)).toBeInTheDocument()
  })

  it('busca la venta y muestra sus líneas', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    fireEvent.change(screen.getByLabelText('Número de venta'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Buscar venta' }))
    expect(await screen.findByText('Venta #42')).toBeInTheDocument()
    expect(screen.getByText('Taladro')).toBeInTheDocument()
  })

  it('devolver todo hace POST /devoluciones con la venta y una Idempotency-Key', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    fireEvent.change(screen.getByLabelText('Número de venta'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Buscar venta' }))
    await screen.findByText('Venta #42')

    fireEvent.click(screen.getByRole('button', { name: 'Devolver todo' }))
    await screen.findByText(/Escribe el número de una venta/i)   // vuelve al inicio tras el éxito

    const llamada = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/devoluciones') && c[1]?.method === 'POST')
    expect(llamada).toBeTruthy()
    expect(JSON.parse(llamada[1].body).venta_id).toBe(5)
    // api() normaliza a Headers; la key idempotente va en el header
    expect(new Headers(llamada[1].headers).get('Idempotency-Key')).toBeTruthy()
  })
})
