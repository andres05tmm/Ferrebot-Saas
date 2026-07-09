/*
 * Contrato landing ↔ dashboard del handoff #token= (smoke del flujo login→handoff→subdominio).
 *
 * Cada mitad tiene sus unitarios (landing/src/lib/auth.test.js, ./handoff.test.js) pero el flujo
 * rompió 3 veces con los unitarios EN VERDE: el contrato (formato de la URL, SLUG_RE espejado,
 * baseDomain espejado, labels reservados) vivía duplicado sin un test que uniera las dos mitades.
 * Este archivo importa AMBOS módulos reales y verifica el viaje completo:
 *   urlDashboardParaTenant (landing) → URL → consumeTokenFromHash + slugFromHost (dashboard).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  esSlugValido,
  urlDashboardParaTenant,
  urlDashboardConToken,
  baseDomain as baseDomainLanding,
} from '../../../landing/src/lib/auth.js'
import { consumeTokenFromHash, slugFromHost, baseDomain as baseDomainDashboard } from './handoff.js'
import { TOKEN_KEY, USER_KEY } from './api'

const BASE = 'melquiadez.com'

// Simula la llegada al dashboard en la URL que construyó la landing (jsdom no permite cambiar
// el host real, así que se le pasa un `win` con la location parseada — la misma seam que usa
// consumeTokenFromHash(win)).
function llegarA(url) {
  const u = new URL(url)
  return {
    win: {
      location: { hash: u.hash, pathname: u.pathname, search: u.search, hostname: u.hostname },
      localStorage: window.localStorage,
      history: { replaceState: vi.fn() },
    },
    u,
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe('contrato handoff landing → dashboard', () => {
  it('el token viaja entero: urlDashboardParaTenant → consumeTokenFromHash', () => {
    const token = 'eyJhbGciOiJIUzI1NiJ9.payload-con_guiones-123.firma'
    const { win, u } = llegarA(urlDashboardParaTenant('barberia-demo', token))

    expect(u.hostname).toBe(`barberia-demo.${BASE}`)
    expect(consumeTokenFromHash(win)).toBe(true)
    expect(localStorage.getItem(TOKEN_KEY)).toBe(token)          // round-trip exacto
    expect(localStorage.getItem(USER_KEY)).toBe(null)            // identidad previa no sobrevive
    expect(win.history.replaceState).toHaveBeenCalled()          // fragmento fuera del historial
    expect(slugFromHost(u.hostname, BASE)).toBe('barberia-demo') // el host SÍ es el tenant
  })

  it('caracteres hostiles en el token sobreviven el encode/decode del fragmento', () => {
    const token = 'a+b/c=&d?e#f'
    const { win } = llegarA(urlDashboardParaTenant('puntorojo', token))
    consumeTokenFromHash(win)
    expect(localStorage.getItem(TOKEN_KEY)).toBe(token)
  })

  it('SLUG_RE está espejado: lo que la landing rechaza, el dashboard también', () => {
    for (const malo of ['Foo', 'a.b', 'ñandu', 'con espacio', '']) {
      expect(esSlugValido(malo)).toBe(false)
      expect(urlDashboardParaTenant(malo, 't')).toBe(null)
    }
    for (const bueno of ['puntorojo', 'barberia-demo', 'x1']) {
      expect(esSlugValido(bueno)).toBe(true)
      expect(slugFromHost(`${bueno}.${BASE}`, BASE)).toBe(bueno)
    }
  })

  it('labels reservados jamás se tratan como tenant en el destino', () => {
    for (const label of ['app', 'api', 'www', 'admin']) {
      expect(slugFromHost(`${label}.${BASE}`, BASE)).toBe(null)
    }
    // El fallback super_admin (sin tenant) va a app. — identidad de plataforma, no un slug.
    expect(new URL(urlDashboardConToken('t')).hostname).toBe(`app.${BASE}`)
  })

  it('baseDomain está espejado: ambos lados derivan el mismo apex en prod', () => {
    for (const host of [`puntorojo.${BASE}`, `app.${BASE}`, BASE]) {
      expect(baseDomainLanding(host)).toBe(BASE)
      expect(baseDomainDashboard(host)).toBe(BASE)
    }
  })
})
