import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'

vi.mock('@microsoft/fetch-event-source', () => ({ fetchEventSource: vi.fn(() => Promise.resolve()) }))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { RealtimeProvider, useRealtimeEvent } from './RealtimeProvider.jsx'

function Sub({ tipos, onHit }) {
  useRealtimeEvent(tipos, onHit)
  return null
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ferrebot_token', 'jwt')
  fetchEventSource.mockClear()
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('RealtimeProvider', () => {
  it('abre UN solo stream y reparte por tipo; "reconnected" llega a sus suscriptores', async () => {
    const a = vi.fn()
    const b = vi.fn()
    render(
      <RealtimeProvider>
        <Sub tipos={['venta_registrada']} onHit={a} />
        <Sub tipos={['inventario_actualizado', 'reconnected']} onHit={b} />
      </RealtimeProvider>,
    )

    expect(fetchEventSource).toHaveBeenCalledTimes(1) // una sola conexión para N suscriptores
    const opts = fetchEventSource.mock.calls[0][1]

    await act(async () => { await opts.onopen({ ok: true, status: 200 }) }) // primera apertura
    act(() => {
      opts.onmessage({ event: 'message', data: JSON.stringify({ event: 'venta_registrada', data: { venta_id: 1 } }) })
    })
    expect(a).toHaveBeenCalledWith('venta_registrada', { venta_id: 1 })
    expect(b).not.toHaveBeenCalled() // b no escucha venta_registrada

    await act(async () => { await opts.onopen({ ok: true, status: 200 }) }) // reapertura → reconnected
    expect(b).toHaveBeenCalledWith('reconnected', {})
  })
})
