import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { isRouteEnabled } from '@/lib/features.jsx'
import TabConversaciones, { haceCuanto } from './TabConversaciones.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const ESCALADA = {
  id: 7, cliente_telefono: '573001112233', estado: 'humano', motivo: 'Pide hablar con un asesor',
  creada_en: '2026-06-07T10:00:00-05:00', escalada_en: '2026-06-07T10:00:00-05:00', resuelta_en: null,
}

function instalarFetch({ escaladas } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    const m = opts.method || 'GET'
    calls.push([u, m, opts.body])
    if (/\/conversaciones\/\d+\/resolver/.test(u)) return Promise.resolve(jsonResp({ id: 7, estado: 'bot' }, 200))
    if (u.includes('/conversaciones/escaladas')) return Promise.resolve(jsonResp(escaladas ?? [ESCALADA]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

function renderTab() {
  return render(<MemoryRouter><TabConversaciones /></MemoryRouter>)
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabConversaciones — gating de ruta', () => {
  it('/conversaciones se oculta sin canal_whatsapp y se ve con la feature', () => {
    expect(isRouteEnabled('/conversaciones', [])).toBe(false)
    expect(isRouteEnabled('/conversaciones', ['pack_agenda'])).toBe(false)  // NO basta pack_agenda
    expect(isRouteEnabled('/conversaciones', ['canal_whatsapp'])).toBe(true)
  })
})

describe('TabConversaciones — bandeja', () => {
  it('lista las conversaciones escaladas (teléfono y motivo)', async () => {
    instalarFetch()
    renderTab()
    expect(await screen.findByText('573001112233')).toBeInTheDocument()
    expect(screen.getByText('Pide hablar con un asesor')).toBeInTheDocument()
  })

  it('estado vacío cuando no hay escaladas', async () => {
    instalarFetch({ escaladas: [] })
    renderTab()
    expect(await screen.findByText('Sin conversaciones en espera')).toBeInTheDocument()
  })

  it('Resolver llama a POST /conversaciones/{id}/resolver', async () => {
    const { calls } = instalarFetch()
    renderTab()
    fireEvent.click(await screen.findByLabelText('Resolver conversación 573001112233'))
    await waitFor(() => {
      expect(calls.some(([u, m]) => u.includes('/conversaciones/7/resolver') && m === 'POST')).toBe(true)
    })
  })

  it('tiempo real: un evento de escalación refetchea la lista', async () => {
    const { calls } = instalarFetch()
    renderTab()
    await screen.findByText('573001112233')
    const antes = calls.filter(([u, m]) => u.includes('/conversaciones/escaladas') && m === 'GET').length

    act(() => { rtHandler?.('conversacion_escalada', { conversacion_id: 9 }) })
    await waitFor(() => {
      const ahora = calls.filter(([u, m]) => u.includes('/conversaciones/escaladas') && m === 'GET').length
      expect(ahora).toBeGreaterThan(antes)
    })
  })
})

describe('haceCuanto', () => {
  it('formatea minutos/horas/días en relativo', () => {
    const base = new Date('2026-06-07T12:00:00-05:00').getTime()
    expect(haceCuanto('2026-06-07T11:58:00-05:00', base)).toBe('hace 2 min')
    expect(haceCuanto('2026-06-07T09:00:00-05:00', base)).toBe('hace 3 h')
    expect(haceCuanto('2026-06-05T12:00:00-05:00', base)).toBe('hace 2 días')
    expect(haceCuanto(null)).toBe('—')
  })
})
