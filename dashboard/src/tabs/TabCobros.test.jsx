import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))

import TabCobros from './TabCobros.jsx'
import { USER_KEY } from '@/lib/api'
import { conQuery } from '@/test/query.jsx'

const COBROS = [
  { id: 1, referencia: 'ped-1-abc', origen: 'pedido', origen_id: 1, cliente_telefono: '3001112233',
    monto: '50000.00', descripcion: 'Pedido #1', estado: 'pendiente', proveedor: 'bold',
    url: 'https://bold.co/pay/x', creado_en: '2026-06-10T14:00:00+00:00', actualizado_en: '2026-06-10T14:00:00+00:00' },
  { id: 2, referencia: 'cita-2-def', origen: 'cita', origen_id: 2, cliente_telefono: null,
    monto: '30000.00', descripcion: 'Anticipo', estado: 'pagado', proveedor: 'bold', url: null,
    creado_en: '2026-06-09T14:00:00+00:00', actualizado_en: '2026-06-09T15:00:00+00:00' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/pagos/cobros/1/pagado-manual')) return Promise.resolve(jsonResp({ ...COBROS[0], estado: 'pagado' }))
    if (u.includes('/pagos/cobros/1/cancelar')) return Promise.resolve(jsonResp({ ...COBROS[0], estado: 'cancelado' }))
    if (u.includes('/pagos/cobros')) return Promise.resolve(jsonResp(COBROS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabCobros', () => {
  it('lista cobros con monto y estado (staff sin acciones)', async () => {
    instalarFetch()   // sin USER_KEY → vendedor
    render(conQuery(<MemoryRouter><TabCobros /></MemoryRouter>))

    expect(await screen.findByText('Pedido #1')).toBeInTheDocument()
    // $50.000 aparece en el KPI "Por cobrar" y en la fila del cobro pendiente
    expect(screen.getAllByText('$50.000').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Anticipo')).toBeInTheDocument()
    // staff: sin botón de marcar pagado
    expect(screen.queryByRole('button', { name: /Marcar pagado/ })).not.toBeInTheDocument()
    expect(screen.getByText(/solo para administradores/i)).toBeInTheDocument()
  })

  it('admin marca pagado un cobro pendiente y llama al endpoint', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabCobros /></MemoryRouter>))
    await screen.findByText('Pedido #1')

    fireEvent.click(screen.getByRole('button', { name: /Marcar pagado el cobro 1/ }))
    await screen.findByText('Pedido #1')

    const llamadas = fetchMock.mock.calls.map(c => [String(c[0]), c[1]?.method])
    expect(llamadas).toContainEqual(['/api/v1/pagos/cobros/1/pagado-manual', 'POST'])
  })

  it('filtra por estado con el query param', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabCobros /></MemoryRouter>))
    await screen.findByText('Pedido #1')

    fireEvent.click(screen.getByRole('button', { name: 'Pagados' }))
    await screen.findByText('Pedido #1')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/pagos/cobros?estado=pagado'))).toBe(true)
  })

  it('se suscribe a los eventos de cobro', async () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabCobros /></MemoryRouter>))
    await screen.findByText('Pedido #1')
    expect(rtEventos).toEqual(expect.arrayContaining(['cobro_creado', 'cobro_pagado', 'cobro_estado']))
  })
})
