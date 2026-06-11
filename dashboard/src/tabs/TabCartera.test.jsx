import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí capturamos el handler (re-fetch) y la lista de eventos.
let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))

import TabCartera from './TabCartera.jsx'
import { USER_KEY } from '@/lib/api.js'

const DEUDORES = [
  { cliente_id: 1, nombre: 'Ana Pérez', telefono: '3001112233', saldo: '150000.00',
    opt_out: false, recordatorios_enviados: 2, ultimo_recordatorio_en: '2026-06-10T14:00:00+00:00',
    promesa_fecha: '2026-06-15' },
  { cliente_id: 2, nombre: 'Bruno Díaz', telefono: '3009998877', saldo: '99000.00',
    opt_out: true, recordatorios_enviados: 0, ultimo_recordatorio_en: null, promesa_fecha: null },
]
const PAGOS = [
  { id: 7, cliente_id: 1, telefono: '3001112233', nota: 'Transferí por Nequi', verificado: false,
    creado_en: '2026-06-11T13:00:00+00:00' },
]
const PROMESAS = [
  { id: 3, cliente_id: 1, telefono: '3001112233', fecha_promesa: '2026-06-15', estado: 'vigente',
    creado_en: '2026-06-11T13:00:00+00:00' },
]
const CONFIG = { activo: true, cadencia_dias: 7, max_recordatorios: 3, hora_inicio: '09:00:00',
  hora_fin: '19:00:00', saldo_minimo: '0.00' }

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/cobranza/deudores')) return Promise.resolve(jsonResp(DEUDORES))
    if (u.includes('/cobranza/pagos-reportados/7/verificar')) return Promise.resolve(jsonResp({ ...PAGOS[0], verificado: true }))
    if (u.includes('/cobranza/pagos-reportados')) return Promise.resolve(jsonResp(PAGOS))
    if (u.includes('/cobranza/promesas')) return Promise.resolve(jsonResp(PROMESAS))
    if (u.includes('/cobranza/config')) return Promise.resolve(jsonResp(CONFIG))
    if (u.includes('/opt-out')) return Promise.resolve(jsonResp(null, 204))
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

describe('TabCartera', () => {
  it('sin rol admin no pide datos y muestra el aviso', () => {
    const fetchMock = instalarFetch()   // sin USER_KEY → vendedor/anónimo
    render(<MemoryRouter><TabCartera /></MemoryRouter>)
    expect(screen.getByText(/solo un administrador/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/cobranza'))).toHaveLength(0)
  })

  it('pinta KPIs, deudores (promesa y opt-out) y pagos por verificar', async () => {
    comoAdmin(); instalarFetch()
    render(<MemoryRouter><TabCartera /></MemoryRouter>)

    expect(await screen.findByText('$249.000')).toBeInTheDocument()   // total en cartera (150k + 99k)
    expect(screen.getByText('Ana Pérez')).toBeInTheDocument()
    expect(screen.getByText(/promete pagar el 2026-06-15/)).toBeInTheDocument()
    expect(screen.getByText('Bruno Díaz')).toBeInTheDocument()
    expect(screen.getByText(/\(sin recordatorios\)/)).toBeInTheDocument()  // opt-out visible
    expect(screen.getByText('Transferí por Nequi', { exact: false })).toBeInTheDocument()
    // Config cargada en el form
    expect(screen.getByLabelText('Cadencia (días)')).toHaveValue(7)
  })

  it('verificar un pago llama al endpoint y refresca la bandeja', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCartera /></MemoryRouter>)
    await screen.findByText('Ana Pérez')

    fireEvent.click(screen.getByRole('button', { name: 'Verificado' }))
    await screen.findByText('Ana Pérez')   // estabiliza tras el refetch

    const llamadas = fetchMock.mock.calls.map(c => [String(c[0]), c[1]?.method])
    expect(llamadas).toContainEqual(['/api/v1/cobranza/pagos-reportados/7/verificar', 'POST'])
  })

  it('el toggle de opt-out llama al endpoint con el valor invertido', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCartera /></MemoryRouter>)
    await screen.findByText('Ana Pérez')

    fireEvent.click(screen.getByRole('button', { name: /Pausar recordatorios de Ana Pérez/ }))
    await screen.findByText('Ana Pérez')

    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/opt-out'))
    expect(String(llamada[0])).toBe('/api/v1/cobranza/clientes/1/opt-out')
    expect(JSON.parse(llamada[1].body)).toEqual({ opt_out: true })
  })

  it('se suscribe a los eventos de cobranza y fiados, y un evento refresca', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabCartera /></MemoryRouter>)
    await screen.findByText('Ana Pérez')

    expect(rtEventos).toEqual(expect.arrayContaining(['promesa_registrada', 'pago_reportado', 'fiado_abonado']))
    const deudoresCalls = () => fetchMock.mock.calls.filter(c => String(c[0]).includes('/cobranza/deudores')).length
    const antes = deudoresCalls()
    await act(async () => { rtHandler('fiado_abonado', {}) })
    expect(deudoresCalls()).toBeGreaterThan(antes)
  })
})
