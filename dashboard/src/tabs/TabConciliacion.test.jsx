import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabConciliacion from './TabConciliacion.jsx'
import { USER_KEY } from '@/lib/api'

const MOVS = [
  {
    movimiento: { id: 10, referencia_bancaria: 'REF-1', fecha: '2026-06-10', monto: '100000',
      naturaleza: 'credito', estado_conciliacion: 'no_conciliado', conciliado_con_tipo: null,
      conciliado_con_id: null, conciliado_en: null },
    candidatos: [{ tipo: 'venta', id: 42, monto: '100000', fecha: '2026-06-10', descripcion: 'Venta #42' }],
  },
  {
    movimiento: { id: 11, referencia_bancaria: 'REF-2', fecha: '2026-06-11', monto: '50000',
      naturaleza: 'debito', estado_conciliacion: 'conciliado', conciliado_con_tipo: 'gasto',
      conciliado_con_id: 7, conciliado_en: '2026-06-11T12:00:00+00:00' },
    candidatos: [],
  },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/bancos/sugerir')) return Promise.resolve(jsonResp({ sugeridos: 1 }))
    if (u.includes('/bancos/movimientos/10/conciliar')) return Promise.resolve(jsonResp({ ...MOVS[0].movimiento, estado_conciliacion: 'conciliado' }))
    if (u.includes('/bancos/movimientos')) return Promise.resolve(jsonResp(MOVS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabConciliacion', () => {
  it('sin rol admin muestra el aviso', () => {
    instalarFetch()
    render(<MemoryRouter><TabConciliacion /></MemoryRouter>)
    expect(screen.getByText(/solo para administradores/i)).toBeInTheDocument()
  })

  it('lista movimientos con candidatos y concilia uno', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabConciliacion /></MemoryRouter>)

    expect(await screen.findByText('REF-1')).toBeInTheDocument()
    expect(screen.getByText(/enlazado con gasto #7/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Conciliar 10 con venta 42/ }))
    await screen.findByText('REF-1')
    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/bancos/movimientos/10/conciliar'))
    expect(JSON.parse(llamada[1].body)).toEqual({ tipo: 'venta', id_interno: 42 })
  })

  it('correr sugerencias llama a POST /bancos/sugerir', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabConciliacion /></MemoryRouter>)
    await screen.findByText('REF-1')

    fireEvent.click(screen.getByRole('button', { name: /Correr sugerencias/ }))
    await screen.findByText('REF-1')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/bancos/sugerir') && c[1]?.method === 'POST')).toBe(true)
  })
})
