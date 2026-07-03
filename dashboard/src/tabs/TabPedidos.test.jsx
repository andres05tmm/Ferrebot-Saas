import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))

import TabPedidos from './TabPedidos.jsx'
import { USER_KEY } from '@/lib/api'

const PEDIDOS = [
  { id: 1, cliente_nombre: 'Ana', cliente_telefono: '3001112233', direccion: 'Cra 1 # 2-3',
    zona_id: null, costo_domicilio: '3000.00', metodo_pago: 'efectivo', estado: 'confirmado',
    subtotal: '36000.00', total: '39000.00', notas: 'sin cebolla', origen: 'whatsapp',
    creado_en: '2026-06-11T17:00:00+00:00', actualizado_en: '2026-06-11T17:00:00+00:00',
    items: [{ id: 1, producto_id: 9, nombre: 'Hamburguesa', cantidad: '2', precio_unitario: '18000.00', subtotal: '36000.00' }] },
  { id: 2, cliente_nombre: null, cliente_telefono: '3009998877', direccion: 'Cl 9',
    zona_id: 1, costo_domicilio: '5000.00', metodo_pago: 'transferencia', estado: 'en_camino',
    subtotal: '20000.00', total: '25000.00', notas: null, origen: 'whatsapp',
    creado_en: '2026-06-11T16:30:00+00:00', actualizado_en: '2026-06-11T17:10:00+00:00',
    items: [{ id: 2, producto_id: 7, nombre: 'Pizza', cantidad: '1', precio_unitario: '20000.00', subtotal: '20000.00' }] },
]
const CONFIG = { activo: true, hora_apertura: '08:00:00', hora_cierre: '21:00:00',
  minimo_pedido: '0.00', tiempo_estimado_min: 45, costo_domicilio_default: '3000.00' }
const ZONAS = [{ id: 1, nombre: 'Bocagrande', tarifa: '8000.00', activo: true }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (/\/pedidos\/\d+\/estado/.test(u)) return Promise.resolve(jsonResp({ ...PEDIDOS[0], estado: 'en_preparacion' }))
    if (u.includes('/pedidos/config')) return Promise.resolve(jsonResp(CONFIG))
    if (u.includes('/pedidos/zonas')) return Promise.resolve(jsonResp(ZONAS))
    if (u.includes('/pedidos')) return Promise.resolve(jsonResp(PEDIDOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() {
  localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' }))
}

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabPedidos', () => {
  it('pinta el kanban por columnas con los pedidos en su estado', async () => {
    instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)

    expect(await screen.findByText('#1 · Ana')).toBeInTheDocument()
    expect(screen.getByText('Confirmados (1)')).toBeInTheDocument()
    expect(screen.getByText('En camino (1)')).toBeInTheDocument()
    expect(screen.getByText('2× Hamburguesa')).toBeInTheDocument()
    expect(screen.getByText('$39.000')).toBeInTheDocument()
    expect(screen.getByText('“sin cebolla”')).toBeInTheDocument()
  })

  it('avanzar llama al endpoint de estado y refresca', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')

    fireEvent.click(screen.getByRole('button', { name: 'A cocina' }))
    await screen.findByText('#1 · Ana')

    const llamada = fetchMock.mock.calls.find(c => /\/pedidos\/1\/estado/.test(String(c[0])))
    expect(llamada[1].method).toBe('PUT')
    expect(JSON.parse(llamada[1].body)).toEqual({ estado: 'en_preparacion' })
  })

  it('sin rol admin no consulta config; con admin pinta reglas y zonas', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/config'))).toHaveLength(0)
    cleanup()

    comoAdmin(); instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    expect(await screen.findByText('Reglas de pedidos')).toBeInTheDocument()
    expect(screen.getByText('Bocagrande')).toBeInTheDocument()
    expect(screen.getByLabelText('Tiempo estimado (min)')).toHaveValue(45)
  })

  it('se suscribe a los eventos del pack y un evento refresca el kanban', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')

    expect(rtEventos).toEqual(['pedido_confirmado', 'pedido_estado'])
    const calls = () => fetchMock.mock.calls.filter(c => /\/pedidos$/.test(String(c[0]))).length
    const antes = calls()
    await act(async () => { rtHandler('pedido_confirmado', {}) })
    expect(calls()).toBeGreaterThan(antes)
  })
})
