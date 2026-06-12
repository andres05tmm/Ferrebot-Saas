import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { consumeTokenFromHash, slugFromHost, landingLoginUrlForHost } from './handoff.js'
import { TOKEN_KEY } from './api.js'

beforeEach(() => {
  localStorage.clear()
  window.history.replaceState(null, '', '/hoy')
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
})

describe('consumeTokenFromHash', () => {
  it('guarda el token del fragmento y limpia el hash sin dejar rastro en el historial', () => {
    window.location.hash = '#token=jwt-abc'
    const replace = vi.spyOn(window.history, 'replaceState')

    const r = consumeTokenFromHash()

    expect(r).toBe(true)
    expect(localStorage.getItem(TOKEN_KEY)).toBe('jwt-abc')
    expect(window.location.hash).toBe('')                 // fragmento limpio
    expect(replace).toHaveBeenCalled()                    // replaceState, no pushState → fuera del historial
  })

  it('preserva path y query al limpiar el fragmento', () => {
    window.history.replaceState(null, '', '/agenda?vista=hoy')
    window.location.hash = '#token=t1'

    consumeTokenFromHash()

    expect(window.location.pathname).toBe('/agenda')
    expect(window.location.search).toBe('?vista=hoy')
    expect(window.location.hash).toBe('')
  })

  it('el token del fragmento REEMPLAZA una sesión previa', () => {
    localStorage.setItem(TOKEN_KEY, 'viejo')
    window.location.hash = '#token=nuevo'

    consumeTokenFromHash()

    expect(localStorage.getItem(TOKEN_KEY)).toBe('nuevo')
  })

  it('sin token en el hash NO toca la sesión y devuelve false (flujo normal)', () => {
    localStorage.setItem(TOKEN_KEY, 'intacto')
    window.location.hash = ''   // sin fragmento

    const r = consumeTokenFromHash()

    expect(r).toBe(false)
    expect(localStorage.getItem(TOKEN_KEY)).toBe('intacto')
  })

  it('un hash sin la clave token (p. ej. ruteo) se ignora', () => {
    window.location.hash = '#/alguna/ruta'
    const r = consumeTokenFromHash()
    expect(r).toBe(false)
  })
})

describe('slugFromHost', () => {
  const BASE = 'melquiadez.com'

  it('extrae el slug de un subdominio de tenant', () => {
    expect(slugFromHost('barberia-demo.melquiadez.com', BASE)).toBe('barberia-demo')
  })

  it('el apex no tiene slug', () => {
    expect(slugFromHost('melquiadez.com', BASE)).toBeNull()
  })

  it('los labels reservados no son tenants (espeja el resolver del backend)', () => {
    expect(slugFromHost('app.melquiadez.com', BASE)).toBeNull()
    expect(slugFromHost('www.melquiadez.com', BASE)).toBeNull()
    expect(slugFromHost('api.melquiadez.com', BASE)).toBeNull()
    expect(slugFromHost('admin.melquiadez.com', BASE)).toBeNull()
  })

  it('un host fuera del dominio base no tiene slug', () => {
    expect(slugFromHost('localhost', BASE)).toBeNull()
    expect(slugFromHost('barberia.otrodominio.com', BASE)).toBeNull()
  })

  it('un subdominio multinivel no es slug', () => {
    expect(slugFromHost('a.b.melquiadez.com', BASE)).toBeNull()
  })
})

describe('landingLoginUrlForHost', () => {
  it('sin landing configurada devuelve null (dev → /login propio)', () => {
    expect(landingLoginUrlForHost('barberia-demo.melquiadez.com')).toBeNull()
  })

  it('con landing configurada arma el login con next = slug del host', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')

    expect(landingLoginUrlForHost('barberia-demo.melquiadez.com'))
      .toBe('https://melquiadez.com/login?next=barberia-demo')
  })

  it('en un host sin slug (app.) rebota a la landing SIN next', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')

    expect(landingLoginUrlForHost('app.melquiadez.com')).toBe('https://melquiadez.com/login')
  })
})
