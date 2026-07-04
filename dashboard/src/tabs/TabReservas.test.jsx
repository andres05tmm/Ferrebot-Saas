import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabReservas from './TabReservas.jsx'
import { conQuery } from '@/test/query.jsx'

const HABS = [
  { recurso_id: 1, nombre: 'Hab 101', precio_noche: '100000', total: '200000' },
  { recurso_id: 2, nombre: 'Hab 102', precio_noche: null, total: null },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/reservas/habitaciones')) return Promise.resolve(jsonResp(HABS))
    if (u.endsWith('/reservas') && opts.method === 'POST') {
      return Promise.resolve(jsonResp({ cita: { id: 9, recurso_id: 1 }, replay: false, anticipo: '50000' }, 201))
    }
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabReservas', () => {
  it('parte pidiendo elegir fechas', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabReservas /></MemoryRouter>))
    expect(screen.getByText(/Elige las fechas y busca/i)).toBeInTheDocument()
  })

  it('busca disponibilidad y lista habitaciones libres con su total', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabReservas /></MemoryRouter>))

    fireEvent.click(screen.getByRole('button', { name: /Buscar disponibilidad/ }))
    expect(await screen.findByText('Hab 101')).toBeInTheDocument()
    expect(screen.getByText('$200.000')).toBeInTheDocument()
    expect(screen.getByText('Hab 102')).toBeInTheDocument()
  })

  it('reserva una habitación llamando a POST /reservas con recurso_id y noches', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabReservas /></MemoryRouter>))

    fireEvent.click(screen.getByRole('button', { name: /Buscar disponibilidad/ }))
    await screen.findByText('Hab 101')

    // abre el form de la primera habitación
    fireEvent.click(screen.getAllByRole('button', { name: 'Reservar' })[0])
    fireEvent.change(screen.getByLabelText('Nombre huésped 1'), { target: { value: 'Ana' } })
    fireEvent.change(screen.getByLabelText('Teléfono huésped 1'), { target: { value: '3001112233' } })
    fireEvent.click(screen.getByRole('button', { name: /Confirmar reserva 1/ }))

    await screen.findByText('Hab 101')
    const llamada = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/reservas') && c[1]?.method === 'POST')
    expect(llamada).toBeTruthy()
    const body = JSON.parse(llamada[1].body)
    expect(body.recurso_id).toBe(1)
    expect(body.noches).toBe(1)
    expect(body.cliente_nombre).toBe('Ana')
  })
})
