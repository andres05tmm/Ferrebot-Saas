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
import { FeaturesProvider } from '@/lib/features.jsx'
import { USER_KEY } from '@/lib/api'

const PEDIDOS = [
  { id: 1, cliente_nombre: 'Ana', cliente_telefono: '3001112233', direccion: 'Cra 1 # 2-3',
    zona_id: null, costo_domicilio: '3000.00', metodo_pago: 'efectivo', estado: 'confirmado',
    subtotal: '36000.00', total: '39000.00', notas: 'sin cebolla', origen: 'whatsapp', pagado: true,
    creado_en: '2026-06-11T17:00:00+00:00', actualizado_en: '2026-06-11T17:00:00+00:00',
    items: [{ id: 1, producto_id: 9, nombre: 'Hamburguesa', cantidad: '2', precio_unitario: '18000.00', subtotal: '36000.00' }] },
  { id: 2, cliente_nombre: null, cliente_telefono: '3009998877', direccion: 'Cl 9',
    zona_id: 1, costo_domicilio: '5000.00', metodo_pago: 'transferencia', estado: 'en_camino',
    subtotal: '20000.00', total: '25000.00', notas: null, origen: 'whatsapp', pagado: false,
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
    // Encabezados renombrados al lenguaje del flujo (confirmado → "Pendientes").
    expect(screen.getByText('Pendientes (1)')).toBeInTheDocument()
    expect(screen.getByText('En preparación (0)')).toBeInTheDocument()
    expect(screen.getByText('En camino (1)')).toBeInTheDocument()
    expect(screen.getByText('Entregados (0)')).toBeInTheDocument()
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

  it('el tab es solo el kanban: no consulta config/zonas ni pinta esos paneles, ni siquiera con admin', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')
    // Reglas y zonas se movieron/salieron del tab: no hay fetch a esos endpoints ni sus paneles.
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/pedidos/config'))).toHaveLength(0)
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/pedidos/zonas'))).toHaveLength(0)
    expect(screen.queryByText('Reglas de pedidos')).toBeNull()
    expect(screen.queryByText('Zonas de domicilio')).toBeNull()
  })

  it('se suscribe a los eventos del pack y un evento refresca el kanban', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')

    expect(rtEventos).toEqual(['pedido_confirmado', 'pedido_estado', 'pedido_pagado'])
    const calls = () => fetchMock.mock.calls.filter(c => /\/pedidos$/.test(String(c[0]))).length
    const antes = calls()
    await act(async () => { rtHandler('pedido_confirmado', {}) })
    expect(calls()).toBeGreaterThan(antes)
  })

  it('muestra la insignia "Pagado" solo en el pedido con cobro pagado', async () => {
    instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')

    // #1 viene pagado en el listado; #2 no.
    expect(screen.getAllByText('Pagado')).toHaveLength(1)
  })

  it('con la feature ventas ofrece "Registrar venta" y llama al endpoint de conversión', async () => {
    const fetchMock = instalarFetch()
    render(
      <MemoryRouter>
        <FeaturesProvider features={['pack_pedidos', 'ventas']}><TabPedidos /></FeaturesProvider>
      </MemoryRouter>,
    )
    await screen.findByText('#1 · Ana')

    // Ambos pedidos vienen sin venta_id → botón en los dos.
    const botones = screen.getAllByRole('button', { name: /Registrar venta/ })
    expect(botones).toHaveLength(2)
    fireEvent.click(botones[0])
    await screen.findByText('#1 · Ana')
    const llamada = fetchMock.mock.calls.find(c => /\/pedidos\/1\/convertir/.test(String(c[0])))
    expect(llamada[1].method).toBe('POST')
  })

  it('sin la feature ventas (o con pedido ya convertido) no hay botón de conversión', async () => {
    instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)   // sin FeaturesProvider → sin `ventas`
    await screen.findByText('#1 · Ana')
    expect(screen.queryByRole('button', { name: /Registrar venta/ })).toBeNull()
  })

  it('al llegar el SSE pedido_pagado la tarjeta se marca pagada sin recargar', async () => {
    instalarFetch()
    render(<MemoryRouter><TabPedidos /></MemoryRouter>)
    await screen.findByText('#1 · Ana')
    expect(screen.getAllByText('Pagado')).toHaveLength(1)   // solo #1

    // El pedido #2 se paga en vivo: la cascada del puente emite pedido_pagado.
    await act(async () => {
      rtHandler('pedido_pagado', { pedido_id: 2, cobro_id: 5, monto: '25000.00' })
    })
    expect(screen.getAllByText('Pagado')).toHaveLength(2)   // #1 y ahora #2
  })
})
