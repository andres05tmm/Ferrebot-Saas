import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, buildAuthHeaders, redirector } from './api'

const realLocation = window.location

// Sustituye window.location por un doble con el host + path deseados (jsdom no deja reasignar
// hostname directo). currentHostname (handoff) lee hostname; el guard anti-bucle de api() lee pathname.
function setHost(hostname: string, pathname = '/hoy') {
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

  // Doble de Response para los 403: clone() devuelve algo cuyo json() resuelve al body dado (o lanza,
  // simulando una respuesta sin JSON). api() solo clona en 403, así que el resto de casos no lo necesita.
  function res403(detail?: string) {
    const json = detail === undefined
      ? () => Promise.reject(new Error('no json'))
      : () => Promise.resolve({ detail })
    return { ok: false, status: 403, clone: () => ({ json }) }
  }

  it('ante 403 cross-tenant en dev limpia la sesión y cae al /login propio (igual que 401)', async () => {
    setHost('localhost')
    localStorage.setItem('ferrebot_token', 'de-otra-empresa')
    localStorage.setItem('ferrebot_user', '{"id":1}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res403('El token no pertenece a esta empresa')))

    await api('/config')

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(localStorage.getItem('ferrebot_user')).toBeNull()
    expect(toLogin).toHaveBeenCalledOnce()
    expect(toLanding).not.toHaveBeenCalled()
  })

  it('ante 403 cross-tenant en prod rebota a la landing con next = slug del host (igual que 401)', async () => {
    setHost('barberia-demo.melquiadez.com')
    localStorage.setItem('ferrebot_token', 'de-otra-empresa')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res403('El token no pertenece a esta empresa')))

    await api('/config')

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(toLanding).toHaveBeenCalledWith('https://melquiadez.com/login?next=barberia-demo')
    expect(toLogin).not.toHaveBeenCalled()
  })

  it('ante 403 de autorización legítima (permisos insuficientes) NO limpia ni rebota: sube al caller', async () => {
    setHost('barberia-demo.melquiadez.com')
    localStorage.setItem('ferrebot_token', 'valido')
    localStorage.setItem('ferrebot_user', '{"id":1}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res403('Permisos insuficientes')))

    const res = await api('/admin/algo')

    expect(res.status).toBe(403)
    expect(localStorage.getItem('ferrebot_token')).toBe('valido')
    expect(localStorage.getItem('ferrebot_user')).toBe('{"id":1}')
    expect(toLogin).not.toHaveBeenCalled()
    expect(toLanding).not.toHaveBeenCalled()
  })

  it('ante 403 sin body JSON NO rompe ni desloguea: sube al caller', async () => {
    setHost('barberia-demo.melquiadez.com')
    localStorage.setItem('ferrebot_token', 'valido')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
    const toLanding = vi.spyOn(redirector, 'toLanding').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res403(undefined)))

    const res = await api('/config')

    expect(res.status).toBe(403)
    expect(localStorage.getItem('ferrebot_token')).toBe('valido')
    expect(toLogin).not.toHaveBeenCalled()
    expect(toLanding).not.toHaveBeenCalled()
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
