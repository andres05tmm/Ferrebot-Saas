import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

// Mock del transporte SSE: capturamos las opciones (onopen/onmessage/onerror) para dispararlas.
// Devuelve una promesa (la lib real lo hace) para que el `.catch(...)` del hook no falle.
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: vi.fn(() => Promise.resolve()),
}))
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { redirector } from '@/lib/api.js'
import { useRealtime } from './useRealtime.js'

function opts() {
  return fetchEventSource.mock.calls[0][1]
}

beforeEach(() => {
  localStorage.clear()
  fetchEventSource.mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useRealtime', () => {
  it('sin token no conecta', () => {
    renderHook(() => useRealtime(() => {}))
    expect(fetchEventSource).not.toHaveBeenCalled()
  })

  it('con token conecta una vez', () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    renderHook(() => useRealtime(() => {}))
    expect(fetchEventSource).toHaveBeenCalledTimes(1)
    expect(fetchEventSource.mock.calls[0][0]).toBe('/api/v1/events')
  })

  it('una reapertura (segundo onopen) emite "reconnected"', async () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    const onEvent = vi.fn()
    renderHook(() => useRealtime(onEvent))

    await opts().onopen({ ok: true, status: 200 }) // primera conexión: NO emite
    expect(onEvent).not.toHaveBeenCalled()
    await opts().onopen({ ok: true, status: 200 }) // reapertura: emite
    expect(onEvent).toHaveBeenCalledWith('reconnected', {})
  })

  it('un 401 en onopen limpia sesión, redirige y no reintenta', async () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    localStorage.setItem('ferrebot_user', '{"id":1}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    renderHook(() => useRealtime(() => {}))

    await expect(opts().onopen({ ok: false, status: 401 })).rejects.toThrow()
    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(toLogin).toHaveBeenCalledOnce()
  })

  it('un mensaje data dispara onEvent(type, data) parseado; el ping se ignora', () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    const onEvent = vi.fn()
    renderHook(() => useRealtime(onEvent))

    opts().onmessage({ event: 'message', data: JSON.stringify({ event: 'venta_registrada', data: { venta_id: 1 } }) })
    expect(onEvent).toHaveBeenCalledWith('venta_registrada', { venta_id: 1 })

    onEvent.mockClear()
    opts().onmessage({ event: 'ping', data: '' })
    expect(onEvent).not.toHaveBeenCalled()
  })
})
