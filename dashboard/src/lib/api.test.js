import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, redirector } from './api.js'

beforeEach(() => {
  localStorage.clear()
  window.history.pushState({}, '', '/hoy')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
})

describe('api wrapper', () => {
  it('apunta a /api/v1 y añade X-Tenant-Slug en dev', async () => {
    vi.stubEnv('DEV', true)
    vi.stubEnv('VITE_TENANT_SLUG', 'puntorojo')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await api('/config')

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/config')
    expect(opts.headers.get('X-Tenant-Slug')).toBe('puntorojo')
  })

  it('NO añade X-Tenant-Slug en producción', async () => {
    vi.stubEnv('DEV', false)
    vi.stubEnv('VITE_TENANT_SLUG', 'puntorojo')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await api('/config')

    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.headers.get('X-Tenant-Slug')).toBeNull()
  })

  it('añade Authorization: Bearer cuando hay token', async () => {
    localStorage.setItem('ferrebot_token', 'jwt-abc')
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await api('/config')

    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.headers.get('Authorization')).toBe('Bearer jwt-abc')
  })

  it('NO añade Authorization cuando no hay token', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await api('/config')

    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.headers.get('Authorization')).toBeNull()
  })

  it('ante 401 limpia la sesión y redirige a /login', async () => {
    localStorage.setItem('ferrebot_token', 'viejo')
    localStorage.setItem('ferrebot_user', '{"id":1}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    await api('/config')

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(localStorage.getItem('ferrebot_user')).toBeNull()
    expect(toLogin).toHaveBeenCalledOnce()
  })

  it('ante 401 estando ya en /login NO redirige (sin bucle)', async () => {
    window.history.pushState({}, '', '/login')
    localStorage.setItem('ferrebot_token', 'viejo')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    await api('/auth/login', { method: 'POST' })

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(toLogin).not.toHaveBeenCalled()
  })
})
