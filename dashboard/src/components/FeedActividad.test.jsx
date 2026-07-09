import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

vi.mock('@microsoft/fetch-event-source', () => ({ fetchEventSource: vi.fn(() => Promise.resolve()) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { RealtimeProvider, useRealtimeStatus } from './RealtimeProvider.jsx'
import FeedActividad from './FeedActividad.jsx'

function PillEstado() {
  const { estado } = useRealtimeStatus()
  return <span data-testid="estado">{estado}</span>
}

function emitir(opts, event, data) {
  act(() => { opts.onmessage({ event: 'message', data: JSON.stringify({ event, data }) }) })
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ferrebot_token', 'jwt')
  fetchEventSource.mockClear()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('useRealtimeStatus', () => {
  it('refleja conectado en onopen y reconectando en onerror', async () => {
    render(<RealtimeProvider><PillEstado /></RealtimeProvider>)
    const opts = fetchEventSource.mock.calls[0][1]
    expect(screen.getByTestId('estado').textContent).toBe('conectando')
    await act(async () => { await opts.onopen({ ok: true, status: 200 }) })
    expect(screen.getByTestId('estado').textContent).toBe('conectado')
    act(() => { opts.onerror(new Error('caído')) })
    expect(screen.getByTestId('estado').textContent).toBe('reconectando')
  })
})

describe('FeedActividad', () => {
  it('pinta los eventos del stream en vivo (venta y transferencia)', async () => {
    render(<RealtimeProvider><FeedActividad /></RealtimeProvider>)
    const opts = fetchEventSource.mock.calls[0][1]
    await act(async () => { await opts.onopen({ ok: true, status: 200 }) })

    expect(screen.getByText(/Aquí aparece en tiempo real/)).toBeInTheDocument()
    emitir(opts, 'venta_registrada', { total: '15000' })
    emitir(opts, 'transferencia_recibida', { remitente: 'MARIA', monto: '20000' })

    expect(await screen.findByText('Venta registrada')).toBeInTheDocument()
    expect(screen.getByText(/Transferencia de MARIA/)).toBeInTheDocument()
    expect(screen.getByText('$20.000')).toBeInTheDocument()
  })

  it("pinta el aviso de pedidos demorados (cron F6) con proveedores y sin monto", async () => {
    render(<RealtimeProvider><FeedActividad /></RealtimeProvider>)
    const opts = fetchEventSource.mock.calls[0][1]
    await act(async () => { await opts.onopen({ ok: true, status: 200 }) })

    emitir(opts, 'pedido_demorado', { pedidos: 2, proveedores: ['Ferrisariato', 'La 80'] })
    expect(await screen.findByText('2 pedidos demorados (Ferrisariato, La 80)')).toBeInTheDocument()

    emitir(opts, 'pedido_demorado', { pedidos: 1, proveedores: ['Ferrisariato'] })
    expect(await screen.findByText('Pedido demorado (Ferrisariato)')).toBeInTheDocument()
  })

  it('el evento interno __estado NO aparece como actividad', async () => {
    render(<RealtimeProvider><FeedActividad /></RealtimeProvider>)
    const opts = fetchEventSource.mock.calls[0][1]
    await act(async () => { await opts.onopen({ ok: true, status: 200 }) })
    // onopen ya emitió __estado; el feed debe seguir vacío.
    expect(screen.getByText(/Aquí aparece en tiempo real/)).toBeInTheDocument()
  })
})
