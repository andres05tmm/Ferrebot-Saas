import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAuth } from './useAuth.js'
import { redirector } from '@/lib/api.js'

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAuth', () => {
  it('loginConPassword(200) guarda token y usuario {id, rol, tenant}', async () => {
    const usuario = { id: 7, rol: 'admin', tenant: 'pr' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ token: 'jwt-xyz', usuario }),
    }))

    const r = await useAuth().loginConPassword('ana@pr.co', 'clave')

    expect(r.ok).toBe(true)
    expect(localStorage.getItem('ferrebot_token')).toBe('jwt-xyz')
    expect(JSON.parse(localStorage.getItem('ferrebot_user'))).toEqual(usuario)
  })

  it('loginConPassword(401) devuelve el mensaje genérico, sin guardar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))

    const r = await useAuth().loginConPassword('ana@pr.co', 'mala')

    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/incorrectos/)
    expect(localStorage.getItem('ferrebot_token')).toBeNull()
  })

  it('logout limpia la sesión y va a /login', () => {
    localStorage.setItem('ferrebot_token', 't')
    localStorage.setItem('ferrebot_user', '{"rol":"admin"}')
    const toLogin = vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})

    useAuth().logout()

    expect(localStorage.getItem('ferrebot_token')).toBeNull()
    expect(localStorage.getItem('ferrebot_user')).toBeNull()
    expect(toLogin).toHaveBeenCalledOnce()
  })

  it('isAdmin refleja el rol guardado', () => {
    localStorage.setItem('ferrebot_user', JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' }))
    expect(useAuth().isAdmin()).toBe(true)
    localStorage.setItem('ferrebot_user', JSON.stringify({ id: 2, rol: 'vendedor', tenant: 'pr' }))
    expect(useAuth().isAdmin()).toBe(false)
  })
})
