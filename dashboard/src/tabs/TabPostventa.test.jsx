import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabPostventa from './TabPostventa.jsx'
import { USER_KEY } from '@/lib/api'
import { conQuery } from '@/test/query.jsx'

const SAT = { promedio: 4.5, respuestas: 8 }
const RESPUESTAS = [
  { id: 1, telefono: '3001112233', calificacion: 5, comentario: 'Excelente servicio', creado_en: '2026-06-10T14:00:00+00:00' },
  { id: 2, telefono: '3009998877', calificacion: 3, comentario: null, creado_en: '2026-06-09T14:00:00+00:00' },
]
const CONFIG = { activo: true, horas_tras_evento: 3, seguir_citas: true, seguir_pedidos: true,
  google_maps_url: null, calificacion_minima_resena: 4 }

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/postventa/satisfaccion')) return Promise.resolve(jsonResp(SAT))
    if (u.includes('/postventa/respuestas')) return Promise.resolve(jsonResp(RESPUESTAS))
    if (u.includes('/postventa/config')) return Promise.resolve(jsonResp(CONFIG))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabPostventa', () => {
  it('sin rol admin no pide datos y muestra el aviso', () => {
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabPostventa /></MemoryRouter>))
    expect(screen.getByText(/solo un administrador/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/postventa'))).toHaveLength(0)
  })

  it('pinta el KPI de satisfacción y las respuestas', async () => {
    comoAdmin(); instalarFetch()
    render(conQuery(<MemoryRouter><TabPostventa /></MemoryRouter>))

    expect(await screen.findByText('4.5')).toBeInTheDocument()
    expect(screen.getByText('Excelente servicio', { exact: false })).toBeInTheDocument()
    // findBy*: el input viene del fetch de config (request distinto al del KPI); en runners lentos
    // puede resolver después — asumirlo síncrono hacía flaky el test en CI.
    expect(await screen.findByLabelText('Horas tras el evento')).toHaveValue(3)
  })
})
