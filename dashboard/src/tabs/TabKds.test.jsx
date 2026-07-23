import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))

import TabKds from './TabKds.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

const KDS = {
  zonas: [{ id: 1, nombre: 'parrilla' }, { id: 2, nombre: 'bar' }],
  comandas: [
    { id: 10, pedido_id: 7, zona_id: 1, zona: 'parrilla', estado: 'pendiente',
      creada_en: '2026-07-23T17:00:00+00:00',
      items: [{ nombre: 'Hamburguesa', cantidad: '2', modificadores: [{ grupo: 'Proteína', opcion: 'Carne', delta_precio: '0.00' }] }] },
    { id: 11, pedido_id: 7, zona_id: 2, zona: 'bar', estado: 'en_preparacion',
      creada_en: '2026-07-23T17:00:00+00:00',
      items: [{ nombre: 'Limonada', cantidad: '1' }] },
  ],
}

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    const json = (data) => Promise.resolve({ ok: true, status: 200, json: async () => data })
    if (/\/kds\/comandas\/\d+\/estado/.test(u)) return json({ ...KDS.comandas[0], estado: 'en_preparacion' })
    if (u.includes('/kds')) return json(KDS)
    return json([])
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabKds', () => {
  it('la ruta /kds se gatea por el flag kds', () => {
    expect(isRouteEnabled('/kds', ['pack_pedidos', 'ventas'])).toBe(false)
    expect(isRouteEnabled('/kds', ['kds', 'pack_pedidos', 'ventas'])).toBe(true)
  })

  it('pinta las columnas por zona con las comandas y sus modificadores', async () => {
    instalarFetch()
    render(<MemoryRouter><TabKds /></MemoryRouter>)
    expect(await screen.findByText('parrilla (1)')).toBeInTheDocument()
    expect(screen.getByText('bar (1)')).toBeInTheDocument()
    expect(screen.getByText('2× Hamburguesa')).toBeInTheDocument()
    expect(screen.getByText('Carne')).toBeInTheDocument()
  })

  it('avanzar llama al endpoint de estado y se suscribe al SSE del pack', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabKds /></MemoryRouter>)
    await screen.findByText('parrilla (1)')
    expect(rtEventos).toEqual(['comanda_nueva', 'comanda_estado', 'pedido_confirmado'])

    fireEvent.click(screen.getByRole('button', { name: 'Iniciar' }))
    await screen.findByText('parrilla (1)')
    const llamada = fetchMock.mock.calls.find(c => /\/kds\/comandas\/10\/estado/.test(String(c[0])))
    expect(llamada[1].method).toBe('PUT')
    expect(JSON.parse(llamada[1].body)).toEqual({ estado: 'en_preparacion' })

    // Evento SSE → refetch (patrón useRealtime).
    const calls = () => fetchMock.mock.calls.filter(c => /\/kds$/.test(String(c[0]))).length
    const antes = calls()
    await act(async () => { rtHandler('comanda_nueva', {}) })
    expect(calls()).toBeGreaterThan(antes)
  })
})
