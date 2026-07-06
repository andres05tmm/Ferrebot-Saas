import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import TabDevoluciones from './TabDevoluciones.jsx'
import { conQuery } from '@/test/query.jsx'

const CUFE = 'ee46939f31987902df19f97aebf248dc85ced9faa485c39461c67af603b7607d'

// Venta facturada en la lista (GET /devoluciones/ventas-facturadas): campos fiscales planos.
const FACTURADA = {
  id: 5, consecutivo: 42, fecha: '2026-06-10T14:00:00+00:00', total: '100000', metodo_pago: 'efectivo',
  fiscal_tipo: 'factura', fiscal_estado: 'aceptada', cufe: CUFE, fiscal_numero: 7, fiscal_prefijo: 'FPR',
}

// Detalle de la venta (GET /ventas/5): cabecera + líneas + estado fiscal.
const VENTA = {
  id: 5, consecutivo: 42, cliente_id: null, vendedor_id: 1, fecha: '2026-06-10T14:00:00+00:00',
  subtotal: '100000', impuestos: '0', total: '100000', metodo_pago: 'efectivo', estado: 'completada',
  origen: 'dashboard', idempotency_key: null,
  fiscal: { tipo: 'factura', estado: 'aceptada', cufe: CUFE, numero: 7, prefijo: 'FPR' },
  lineas: [{ producto_id: 7, descripcion: 'Taladro', cantidad: '1', precio_unitario: '100000', iva: 0 }],
}

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ facturadas = [FACTURADA] } = {}) {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.endsWith('/devoluciones') && opts.method === 'POST') {
      return Promise.resolve(jsonResp({ id: 1, venta_id: 5, total: '100000', metodo_reintegro: 'efectivo', estado: 'ok' }, 201))
    }
    if (u.includes('/devoluciones/ventas-facturadas')) return Promise.resolve(jsonResp(facturadas))
    if (u.includes('/ventas/5')) return Promise.resolve(jsonResp(VENTA))
    return Promise.resolve(jsonResp({}, 404))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabDevoluciones', () => {
  it('lista las ventas facturadas (número, documento y CUFE)', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    expect(await screen.findByText('N.º 42')).toBeInTheDocument()
    expect(screen.getByText(/Factura · aceptada/i)).toBeInTheDocument()          // badge fiscal
    expect(screen.getByText(new RegExp(CUFE.slice(0, 20)))).toBeInTheDocument()  // CUFE visible
  })

  it('la búsqueda por CUFE consulta el endpoint con q', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    await screen.findByText('N.º 42')
    fireEvent.change(screen.getByLabelText('Buscar venta facturada'), { target: { value: 'ee46939f' } })
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('ventas-facturadas?q=ee46939f'))).toBe(true))
  })

  it('elegir una venta carga sus líneas y el aviso de nota crédito', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    fireEvent.click(await screen.findByText('N.º 42'))
    expect(await screen.findByText('Venta #42')).toBeInTheDocument()
    expect(screen.getByText('Taladro')).toBeInTheDocument()
    expect(screen.getByText(/Al devolver se emite la nota crédito/i)).toBeInTheDocument()
  })

  it('devolver todo hace POST /devoluciones con la venta y una Idempotency-Key', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    fireEvent.click(await screen.findByText('N.º 42'))
    await screen.findByText('Venta #42')

    fireEvent.click(screen.getByRole('button', { name: 'Devolver todo' }))
    await screen.findByText('N.º 42')   // vuelve a la lista tras el éxito

    const llamada = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/devoluciones') && c[1]?.method === 'POST')
    expect(llamada).toBeTruthy()
    expect(JSON.parse(llamada[1].body).venta_id).toBe(5)
    expect(new Headers(llamada[1].headers).get('Idempotency-Key')).toBeTruthy()
  })

  it('sin ventas facturadas muestra el vacío', async () => {
    instalarFetch({ facturadas: [] })
    render(conQuery(<MemoryRouter><TabDevoluciones /></MemoryRouter>))
    expect(await screen.findByText(/No hay ventas con factura POS o electrónica/i)).toBeInTheDocument()
  })
})
