/*
 * Login de la landing contra la API real (ADR 0009): POST /api/v1/auth/login/password.
 * Al éxito, el handoff al dashboard va por FRAGMENTO de URL (#token=...): no viaja al
 * servidor ni queda en logs. El dashboard lo lee al arrancar (rama de backend aparte).
 *
 * Destino del handoff: el SUBDOMINIO del tenant (`{slug}.{base}`), no `app.`. En el subdominio el
 * resolver saca el tenant de la señal primaria y fiable (el host) → GET /config responde 200 y el
 * dashboard nace tematizado. `app.` es label reservado (sin tenant) → solo sirve de fallback para la
 * identidad de plataforma (super_admin, sin tenant). El slug se valida contra el MISMO contrato que
 * dashboard/src/lib/handoff.js (^[a-z0-9-]+$): jamás se trata `next` como URL → cero open redirect.
 */

// Contrato del slug — ESPEJA dashboard/src/lib/handoff.js SLUG_RE y core/tenancy/resolver.py.
const SLUG_RE = /^[a-z0-9-]+$/
const BASE_DOMAIN_DEFAULT = 'melquiadez.com'

// Mensajes alineados con la API (401 genérico sin enumeración; 429 lockout).
export const MENSAJES = {
  credenciales: 'Email o contraseña incorrectos.',
  bloqueado: 'Demasiados intentos. Espera unos minutos y vuelve a intentar.',
  conexion: 'No pudimos conectar. Revisa tu internet e intenta de nuevo.',
  // Guard de aislamiento: el cliente llegó por el link de UNA empresa (`?next`) pero sus credenciales
  // son de otra. Se rechaza en la puerta; jamás se le enruta a otro dashboard. Sin revelar de cuál.
  otraEmpresa: 'Estas credenciales son de otro negocio. Entra desde melquiadez.com o usa el enlace de tu empresa.',
}

/** ¿`s` es un slug de tenant válido? (mismo contrato que el resolver y el dashboard). */
export function esSlugValido(s) {
  return typeof s === 'string' && SLUG_RE.test(s)
}

function currentHostname() {
  return typeof window !== 'undefined' ? window.location.hostname : ''
}

// IP literal (IPv4 o IPv6) → host de dev, sin apex derivable.
function isIpAddress(host) {
  if (host.includes(':')) return true
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host)
}

/**
 * baseDomain — apex registrable del despliegue, derivado IGUAL que en el dashboard (handoff.js):
 * override por env (VITE_BASE_DOMAIN); si no, los dos últimos labels del host runtime; en dev
 * (localhost / IP / un solo label) cae al default melquiadez.com.
 */
export function baseDomain(hostname = currentHostname()) {
  const env = import.meta.env.VITE_BASE_DOMAIN
  if (env) return env
  const host = (hostname || '').toLowerCase().trim()
  if (host && host !== 'localhost' && !host.endsWith('.localhost') && !isIpAddress(host)) {
    const labels = host.split('.')
    if (labels.length >= 2) return labels.slice(-2).join('.')
  }
  return BASE_DOMAIN_DEFAULT
}

// API y dashboard viven bajo `app.{base}` (mismo origin: apps/api sirve el SPA). Derivados del host
// en RUNTIME —igual que baseDomain()—: un deploy de staging en otro dominio jamás debe POSTear
// credenciales a la API de producción por un fallback hardcodeado. Las VITE_ vars son override.
export const API_URL = import.meta.env.VITE_API_URL || `https://app.${baseDomain()}`
export const APP_URL = import.meta.env.VITE_APP_URL || `https://app.${baseDomain()}`

/**
 * urlDashboardParaTenant — destino del handoff al SUBDOMINIO del tenant, con el token en el fragmento.
 * Devuelve null si el slug no es válido (caller cae a urlDashboardConToken → app, identidad de plataforma).
 */
export function urlDashboardParaTenant(slug, token) {
  if (!esSlugValido(slug)) return null
  return `https://${slug}.${baseDomain()}/#token=${encodeURIComponent(token)}`
}

/** URL del dashboard de plataforma (app., sin tenant) con el token en el fragmento. Fallback super_admin. */
export function urlDashboardConToken(token) {
  return `${APP_URL}/#token=${encodeURIComponent(token)}`
}

/**
 * Autentica contra la API. Devuelve { ok: true, token, usuario } o { ok: false, error }.
 * Nunca lanza: los errores de red se traducen a un mensaje amable.
 */
export async function iniciarSesion(email, password, fetcher = fetch) {
  try {
    const res = await fetcher(`${API_URL}/api/v1/auth/login/password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (res.ok) {
      const datos = await res.json()
      return { ok: true, token: datos.token, usuario: datos.usuario }
    }
    if (res.status === 429) return { ok: false, error: MENSAJES.bloqueado }
    return { ok: false, error: MENSAJES.credenciales }
  } catch {
    return { ok: false, error: MENSAJES.conexion }
  }
}
