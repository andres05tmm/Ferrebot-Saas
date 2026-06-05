import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api.js'

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
})
