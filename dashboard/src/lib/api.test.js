import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, buildAuthHeaders, redirector } from './api.js'

const realLocation = window.location

// Sustituye window.location por un doble con el host + path deseados (jsdom no deja reasignar
// hostname directo). currentHostname (handoff) lee hostname; el guard anti-bucle de api() lee pathname.
function setHost(hostname, pathname = '/hoy') {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...realLocation, hostname, pathname, replace: vi.fn() },
  })
}

beforeEach(() => {
  localStorage.clear()
  window.history.pushState({}, '', '/hoy')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
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

  it('ante 401 en dev (sin landing) limpia la sesión y cae al /login propio', async () => {
    setHost('localhost')   // dev: landingLoginUrlForHost → null
    localStorage.setItem('ferrebot_token', 'viejo')
    localStorage.setItem('ferrebot_user', '{"id":1}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    await api('/config')

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(localStorage.getItem('ferrebot_user')).toBeNull()
    expect(toLogin).toHaveBeenCalledOnce()
    expect(toLanding).not.toHaveBeenCalled()
  })

  it('ante 401 en prod (subdominio tenant) rebota a la landing con next = slug del host', async () => {
    setHost('barberia-demo.melquiadez.com')   // prod: deriva landing del host en runtime
    localStorage.setItem('ferrebot_token', 'viejo')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    await api('/config')

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(toLanding).toHaveBeenCalledWith('https://melquiadez.com/login?next=barberia-demo')
    expect(toLogin).not.toHaveBeenCalled()
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

describe('buildAuthHeaders', () => {
  it('incluye Bearer y X-Tenant-Slug en dev', () => {
    vi.stubEnv('DEV', true)
    vi.stubEnv('VITE_TENANT_SLUG', 'puntorojo')
    localStorage.setItem('ferrebot_token', 'jwt-1')

    const h = buildAuthHeaders()

    expect(h.Authorization).toBe('Bearer jwt-1')
    expect(h['X-Tenant-Slug']).toBe('puntorojo')
  })

  it('sin token no pone Authorization', () => {
    vi.stubEnv('DEV', true)
    const h = buildAuthHeaders()
    expect(h.Authorization).toBeUndefined()
  })
})
