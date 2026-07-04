import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabKardex from './TabKardex.jsx'
import { conQuery } from '@/test/query.jsx'

const PRODUCTOS = [{ id: 5, nombre: 'Taladro Bosch', codigo: 'TAL-01', precio_venta: '120000' }]
const KARDEX = [
  { id: 100, tipo: 'VENTA', cantidad: '2', costo_unitario: '80000', referencia: 'venta #12',
    usuario_id: 1, creado_en: '2026-06-10T14:00:00+00:00' },
  { id: 101, tipo: 'COMPRA', cantidad: '10', costo_unitario: '75000', referencia: 'compra #3',
    usuario_id: 1, creado_en: '2026-06-08T14:00:00+00:00' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/inventario/kardex/5')) return Promise.resolve(jsonResp(KARDEX))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabKardex', () => {
  it('parte con un estado vacío que invita a buscar', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabKardex /></MemoryRouter>))
    expect(screen.getByText(/Busca un producto para ver su historial/i)).toBeInTheDocument()
  })

  it('busca, selecciona un producto y muestra sus movimientos', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabKardex /></MemoryRouter>))

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'taladro' } })
    fireEvent.click(await screen.findByRole('button', { name: /Taladro Bosch/ }))

    expect(await screen.findByText('VENTA')).toBeInTheDocument()
    expect(screen.getByText('COMPRA')).toBeInTheDocument()
    expect(screen.getByText(/venta #12/)).toBeInTheDocument()
    // salida negativa, entrada positiva
    expect(screen.getByText('−2')).toBeInTheDocument()
    expect(screen.getByText('+10')).toBeInTheDocument()
  })
})
