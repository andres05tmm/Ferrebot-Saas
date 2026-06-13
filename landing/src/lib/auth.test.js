import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_URL, MENSAJES, esSlugValido, iniciarSesion, urlDashboardConToken, urlDashboardParaTenant } from './auth.js'

afterEach(() => vi.unstubAllEnvs())

const respuesta = (status, body = {}) =>
  Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) })

describe('iniciarSesion', () => {
  it('hace POST al endpoint real con email y contraseña', async () => {
    const fetcher = vi.fn(() => respuesta(200, { token: 't', usuario: { rol: 'admin' } }))
    await iniciarSesion('ana@negocio.com', 'clave123', fetcher)
    expect(fetcher).toHaveBeenCalledWith(
      `${API_URL}/api/v1/auth/login/password`,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ email: 'ana@negocio.com', password: 'clave123' }),
      }),
    )
  })

  it('al éxito devuelve token y usuario', async () => {
    const fetcher = () => respuesta(200, { token: 'jwt-abc', usuario: { id: 1, rol: 'admin', tenant: 'brasa' } })
    const res = await iniciarSesion('a@b.c', 'x', fetcher)
    expect(res).toEqual({ ok: true, token: 'jwt-abc', usuario: { id: 1, rol: 'admin', tenant: 'brasa' } })
  })

  it('401 → mensaje genérico (sin enumeración de usuarios)', async () => {
    const res = await iniciarSesion('a@b.c', 'mala', () => respuesta(401))
    expect(res).toEqual({ ok: false, error: MENSAJES.credenciales })
  })

  it('429 → bloqueado, reintenta luego', async () => {
    const res = await iniciarSesion('a@b.c', 'x', () => respuesta(429))
    expect(res).toEqual({ ok: false, error: MENSAJES.bloqueado })
  })

  it('error de red → mensaje de conexión, nunca lanza', async () => {
    const res = await iniciarSesion('a@b.c', 'x', () => Promise.reject(new TypeError('failed')))
    expect(res).toEqual({ ok: false, error: MENSAJES.conexion })
  })
})

describe('handoff al dashboard', () => {
  it('manda el token por fragmento (#token=…), URL-encodeado', () => {
    const url = urlDashboardConToken('ey.J/W+T=')
    expect(url).toMatch(/#token=ey\.J%2FW%2BT%3D$/)
    expect(url.startsWith('https://')).toBe(true)
    expect(url).not.toContain('?token')
  })
})

describe('esSlugValido (contrato ^[a-z0-9-]+$)', () => {
  it('acepta slugs de tenant válidos', () => {
    expect(esSlugValido('barberia-demo')).toBe(true)
    expect(esSlugValido('puntorojo')).toBe(true)
  })
  it('rechaza vacíos, mayúsculas, puntos y caracteres raros', () => {
    expect(esSlugValido('')).toBe(false)
    expect(esSlugValido('Barberia')).toBe(false)
    expect(esSlugValido('a.b')).toBe(false)
    expect(esSlugValido('../evil')).toBe(false)
    expect(esSlugValido(null)).toBe(false)
    expect(esSlugValido(undefined)).toBe(false)
  })
})

describe('urlDashboardParaTenant (handoff al subdominio del tenant)', () => {
  it('apunta a {slug}.melquiadez.com con el token en el fragmento, URL-encodeado', () => {
    const url = urlDashboardParaTenant('barberia-demo', 'ey.J/W+T=')
    expect(url).toBe('https://barberia-demo.melquiadez.com/#token=ey.J%2FW%2BT%3D')
    expect(url).not.toContain('?token')
  })

  it('devuelve null si el slug es inválido (caller cae a app.)', () => {
    expect(urlDashboardParaTenant('', 't')).toBeNull()
    expect(urlDashboardParaTenant('Bad Slug!', 't')).toBeNull()
    expect(urlDashboardParaTenant(null, 't')).toBeNull()
    expect(urlDashboardParaTenant(undefined, 't')).toBeNull()
  })

  it('VITE_BASE_DOMAIN gana como override del apex', () => {
    vi.stubEnv('VITE_BASE_DOMAIN', 'staging.example')
    expect(urlDashboardParaTenant('brasa', 't')).toBe('https://brasa.staging.example/#token=t')
  })
})
