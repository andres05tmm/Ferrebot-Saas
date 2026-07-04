import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos) => { rtEventos = tipos },
}))

import TabCotizaciones from './TabCotizaciones.jsx'
import { USER_KEY } from '@/lib/api'

const COTS = [
  { id: 1, cliente_telefono: '3001112233', cliente_nombre: 'Ana Pérez', estado: 'pendiente',
    total: '120000.00', vigencia_hasta: '2026-06-20', creado_en: '2026-06-10T14:00:00+00:00',
    actualizado_en: '2026-06-10T14:00:00+00:00',
    items: [{ id: 10, producto_id: 5, nombre: 'Taladro', cantidad: '1', precio_unitario: '120000.00', subtotal: '120000.00' }] },
  { id: 2, cliente_telefono: '3009998877', cliente_nombre: null, estado: 'aceptada',
    total: '50000.00', vigencia_hasta: null, creado_en: '2026-06-09T14:00:00+00:00',
    actualizado_en: '2026-06-09T15:00:00+00:00', items: [] },
]
const CONFIG = { mostrar_stock: true, vigencia_dias: 3 }

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/cotizaciones/1/estado')) return Promise.resolve(jsonResp({ ...COTS[0], estado: 'aceptada' }))
    if (u.includes('/cotizaciones/config')) return Promise.resolve(jsonResp(CONFIG))
    if (u.includes('/cotizaciones')) return Promise.resolve(jsonResp(COTS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear(); rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCotizaciones', () => {
  it('lista cotizaciones con total y estado', async () => {
    instalarFetch()
    render(<MemoryRouter><TabCotizaciones /></MemoryRouter>)
    expect(await screen.findByText('Ana Pérez')).toBeInTheDocument()
    expect(screen.getByText('$120.000')).toBeInTheDocument()
    expect(screen.getByText('3009998877')).toBeInTheDocument()   // sin nombre → teléfono
  })

  it('aceptar una cotización pendiente llama al endpoint con el estado', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCotizaciones /></MemoryRouter>)
    await screen.findByText('Ana Pérez')

    fireEvent.click(screen.getByRole('button', { name: /Aceptar cotización 1/ }))
    await screen.findByText('Ana Pérez')

    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/cotizaciones/1/estado'))
    expect(llamada[1].method).toBe('PUT')
    expect(JSON.parse(llamada[1].body)).toEqual({ estado: 'aceptada' })
  })

  it('el admin ve la config del cotizador; el staff no la pide', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCotizaciones /></MemoryRouter>)
    await screen.findByText('Ana Pérez')
    expect(await screen.findByLabelText('Vigencia (días)')).toHaveValue(3)

    cleanup()
    localStorage.clear()
    const fetchStaff = instalarFetch()
    render(<MemoryRouter><TabCotizaciones /></MemoryRouter>)
    await screen.findByText('Ana Pérez')
    expect(fetchStaff.mock.calls.some(c => String(c[0]).includes('/cotizaciones/config'))).toBe(false)
  })
})
