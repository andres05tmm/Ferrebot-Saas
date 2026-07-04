import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { consumeTokenFromHash, slugFromHost, landingLoginUrlForHost, baseDomain, landingOrigin } from './handoff.js'
import { TOKEN_KEY } from './api'

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

describe('baseDomain (derivación en runtime)', () => {
  it('la env VITE_BASE_DOMAIN gana como override explícito', () => {
    vi.stubEnv('VITE_BASE_DOMAIN', 'override.example')
    expect(baseDomain('barberia-demo.melquiadez.com')).toBe('override.example')
  })

  it('sin env, deriva el apex (dos últimos labels) de un subdominio de tenant', () => {
    expect(baseDomain('barberia-demo.melquiadez.com')).toBe('melquiadez.com')
  })

  it('sin env, app.melquiadez.com también deriva melquiadez.com', () => {
    expect(baseDomain('app.melquiadez.com')).toBe('melquiadez.com')
  })

  it('sin env, el apex se deriva a sí mismo', () => {
    expect(baseDomain('melquiadez.com')).toBe('melquiadez.com')
  })

  it('sin env, en dev (localhost / IP / un solo label) no hay base domain', () => {
    expect(baseDomain('localhost')).toBe('')
    expect(baseDomain('app.localhost')).toBe('')
    expect(baseDomain('127.0.0.1')).toBe('')
    expect(baseDomain('mimaquina')).toBe('')
  })
})

describe('landingOrigin (derivación en runtime)', () => {
  it('la env VITE_LANDING_ORIGIN gana como override explícito', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://landing.example')
    expect(landingOrigin('barberia-demo.melquiadez.com')).toBe('https://landing.example')
  })

  it('sin env, deriva https://{baseDomain} en prod', () => {
    expect(landingOrigin('barberia-demo.melquiadez.com')).toBe('https://melquiadez.com')
  })

  it('sin env, en dev (sin base domain) devuelve cadena vacía', () => {
    expect(landingOrigin('localhost')).toBe('')
  })
})

describe('landingLoginUrlForHost', () => {
  it('en dev (localhost) devuelve null → /login propio del dashboard', () => {
    expect(landingLoginUrlForHost('localhost')).toBeNull()
    expect(landingLoginUrlForHost('127.0.0.1')).toBeNull()
  })

  it('sin env, deriva el login de la landing del host con next = slug', () => {
    expect(landingLoginUrlForHost('barberia-demo.melquiadez.com'))
      .toBe('https://melquiadez.com/login?next=barberia-demo')
  })

  it('sin env, en un host sin slug (app.) rebota a la landing SIN next', () => {
    expect(landingLoginUrlForHost('app.melquiadez.com')).toBe('https://melquiadez.com/login')
  })

  it('la env gana como override: arma el login con next = slug del host', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')

    expect(landingLoginUrlForHost('barberia-demo.melquiadez.com'))
      .toBe('https://melquiadez.com/login?next=barberia-demo')
  })

  it('la env gana como override: host sin slug (app.) rebota SIN next', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')

    expect(landingLoginUrlForHost('app.melquiadez.com')).toBe('https://melquiadez.com/login')
  })
})
