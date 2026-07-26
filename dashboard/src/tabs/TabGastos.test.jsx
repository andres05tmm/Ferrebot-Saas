import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))
const authState = { admin: true }
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))
// La bandeja del bot es un panel aparte (construcción); aquí solo estorba.
vi.mock('./construccion/BandejaRevision.jsx', () => ({ default: () => null }))

import TabGastos from './TabGastos.jsx'

const GASTOS = [
  { id: 1, categoria: 'transporte', tipo_egreso: 'gasto', monto: '5000.00', concepto: 'Taxi', caja_id: 1, usuario_id: 1, creado_en: '2026-06-05T14:00:00+00:00' },
  { id: 2, categoria: 'otros', tipo_egreso: 'retiro', monto: '200000.00', concepto: 'Para la casa', caja_id: 1, usuario_id: 1, creado_en: '2026-06-05T15:00:00+00:00' },
]

const RESUMEN = {
  desde: '2026-06-01', hasta: '2026-06-05',
  total_gasto: '900000.00', total_retiro: '200000.00', total_inversion: '0.00',
  total_pago_deuda: '0.00', fijos: '700000.00', variables: '200000.00',
  por_categoria: [
    { categoria: 'arriendo', total: '700000.00', cantidad: 1, fijo: true, pct: '77.78' },
    { categoria: 'empaque', total: '200000.00', cantidad: 3, fijo: false, pct: '22.22' },
  ],
  gasto_periodo_anterior: '750000.00',
  ventas: '4500000.00', pct_ventas: '20.00',
  margen_bruto_pct: '25.00', fijos_mes: '700000.00',
  punto_equilibrio_mes: '2800000.00', punto_equilibrio_dia: '93333.00',
}

const RECURRENTES = [
  { id: 1, nombre: 'Arriendo', categoria: 'arriendo', monto_estimado: '700000.00', dia_mes: 5, activo: true, pagado_en: '2026-06-05T10:00:00+00:00', gasto_id: 7, monto_pagado: '700000.00' },
  { id: 2, nombre: 'Internet', categoria: 'servicios', monto_estimado: '90000.00', dia_mes: 10, activo: true, pagado_en: null, gasto_id: null, monto_pagado: null },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (opts?.method === 'POST' || opts?.method === 'PUT') return Promise.resolve(jsonResp({ id: 9 }, 201))
    if (u.includes('/reportes/gastos')) return Promise.resolve(jsonResp(RESUMEN))
    if (u.includes('/gastos/recurrentes')) return Promise.resolve(jsonResp(RECURRENTES))
    if (u.includes('/gastos')) return Promise.resolve(jsonResp(GASTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const postA = (mock, frag) => mock.mock.calls.find(
  (c) => String(c[0]).includes(frag) && c[1]?.method === 'POST'
)

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabGastos', () => {
  it('lista los movimientos del período y marca los que NO son gasto', async () => {
    instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)

    expect(await screen.findByText('Taxi')).toBeInTheDocument()
    // El retiro se ve, pero etiquetado: salió de la caja y no es gasto.
    const fila = (await screen.findByText('Para la casa')).closest('li')
    expect(within(fila).getByText('Retiro')).toBeInTheDocument()
  })

  it('muestra los números del dueño: peso sobre la venta y cuánto vender para no perder', async () => {
    instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)

    expect(await screen.findByText('$900.000')).toBeInTheDocument()          // gasto del período
    expect(screen.getByText('20%')).toBeInTheDocument()                       // % de la venta
    expect(screen.getByText('$93.333 / día')).toBeInTheDocument()             // punto de equilibrio
    expect(screen.getByText(/\+20% vs\. el período anterior/)).toBeInTheDocument()
    // Desglose separando fijos de variables (el corte que define el equilibrio).
    expect(screen.getByText(/Se pagan igual vendas mucho o nada/)).toBeInTheDocument()
    // "Arriendo" también está en el checklist de recurrentes: se acota al desglose.
    const desglose = screen.getByText('En qué se fue').parentElement
    expect(within(desglose).getByText('Arriendo')).toBeInTheDocument()
    expect(within(desglose).getByText('Empaque (bolsas, potes)')).toBeInTheDocument()
  })

  it('el vendedor no pide el resumen (márgenes y utilidad son de admin)', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)

    await screen.findByText('Taxi')
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/reportes/gastos'))).toBe(false)
    expect(screen.queryByText(/Para no perder/)).not.toBeInTheDocument()
  })

  it('registrar manda el tipo de egreso (un retiro NO viaja como gasto)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)
    await screen.findByText('Taxi')

    fireEvent.click(screen.getByRole('button', { name: /Registrar/ }))
    fireEvent.change(await screen.findByLabelText('Monto'), { target: { value: '150000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Retiro' }))
    fireEvent.click(screen.getByRole('button', { name: /^Registrar$/ }))

    await waitFor(() => expect(postA(fetchMock, '/gastos')).toBeTruthy())
    const call = postA(fetchMock, '/gastos')
    expect(new Headers(call[1].headers).get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(call[1].body)).toMatchObject({ monto: 150000, tipo_egreso: 'retiro' })
  })

  it('filtrar por categoría se resuelve en el servidor (no en el cliente)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)
    await screen.findByText('Taxi')

    fireEvent.change(screen.getByLabelText('Filtrar por categoría'), { target: { value: 'empaque' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('categoria=empaque'))).toBe(true)
    })
  })

  it('anular un gasto mal digitado postea la reversa (no lo borra)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)
    await screen.findByText('Taxi')

    fireEvent.click(screen.getByRole('button', { name: /Anular gasto de \$5\.000/ }))
    fireEvent.change(await screen.findByLabelText('Motivo (opcional)'), { target: { value: 'monto malo' } })
    fireEvent.click(screen.getByRole('button', { name: /^Anular$/ }))

    await waitFor(() => expect(postA(fetchMock, '/gastos/1/anular')).toBeTruthy())
    expect(JSON.parse(postA(fetchMock, '/gastos/1/anular')[1].body)).toEqual({ motivo: 'monto malo' })
  })

  it('el checklist del mes dice qué falta y paga con el vínculo al recurrente', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabGastos /></MemoryRouter>)

    expect(await screen.findByText('Internet')).toBeInTheDocument()
    expect(screen.getByText(/Faltan 1 por pagar/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Pagar' }))
    expect(await screen.findByText('Pagar Internet')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Registrar$/ }))

    await waitFor(() => expect(postA(fetchMock, '/gastos')).toBeTruthy())
    // Monto prellenado con el estimado y vínculo al recurrente: el checklist lo da por pagado.
    expect(JSON.parse(postA(fetchMock, '/gastos')[1].body)).toMatchObject({
      recurrente_id: 2, categoria: 'servicios', monto: 90000,
    })
  })
})
