/*
 * handoff.js — puente landing ↔ dashboard (plan Melquiadez §3, M4).
 *
 * Dos direcciones:
 *  1. ENTRADA: la landing (melquiadez.com/login) hace POST al login de la API, recibe el {token} y
 *     redirige a `https://app.melquiadez.com/#token=...`. Al arrancar la SPA, `consumeTokenFromHash`
 *     lee ese fragmento (no viaja al servidor, no queda en logs), lo guarda como la sesión (mismo
 *     TOKEN_KEY/localStorage que useAuth) y LO BORRA del historial — antes de que arranque nada más.
 *  2. SALIDA: si un request sin sesión cae en `{slug}.melquiadez.com`, el dashboard rebota a la landing
 *     (`landingLoginUrlForHost`) con el slug DEL HOST actual como `next` — nunca input del usuario.
 *
 * Origins/dominios por env (Vite): VITE_LANDING_ORIGIN, VITE_BASE_DOMAIN. Sin configurar (dev local)
 * NO hay landing → el dashboard usa su propio /login.
 */
import { TOKEN_KEY } from './api.js'

// Labels que NO son tenants — ESPEJA core/tenancy/resolver.py LABELS_RESERVADOS. `app.melquiadez.com`
// (entrada de clientes) no tiene slug, así que su rebote a la landing va SIN `next`.
const LABELS_RESERVADOS = new Set(['app', 'api', 'www', 'admin'])

// Contrato del slug acordado con la landing (ver landingLoginUrlForHost).
const SLUG_RE = /^[a-z0-9-]+$/

export function landingOrigin() {
  return import.meta.env.VITE_LANDING_ORIGIN || ''
}

export function baseDomain() {
  return import.meta.env.VITE_BASE_DOMAIN || ''
}

/**
 * consumeTokenFromHash — handoff de ENTRADA. Si la URL trae `#token=...`, guarda el token y limpia el
 * fragmento con history.replaceState (NO pushState → jamás entra al historial), preservando path+query.
 * Si ya había sesión, el token del fragmento la REEMPLAZA. Devuelve true si consumió un token.
 *
 * Debe correr ANTES de montar el router/cualquier fetch (se llama en main.jsx, antes de createRoot).
 */
export function consumeTokenFromHash(win = window) {
  const hash = win.location?.hash || ''
  if (!hash.includes('token=')) return false
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const token = params.get('token')
  if (!token) return false
  try { win.localStorage.setItem(TOKEN_KEY, token) } catch {}
  // Limpia SOLO el fragmento (deja path + query intactos). replaceState → no deja rastro en el historial.
  const { pathname, search } = win.location
  win.history.replaceState(null, '', `${pathname}${search}`)
  return true
}

/**
 * slugFromHost — deriva el slug del tenant del HOST actual (jamás de input del usuario). Devuelve null
 * para el apex, un label reservado (app/api/www/admin), un host fuera de BASE_DOMAIN, o un subdominio
 * multinivel. Espeja core/tenancy/resolver._slug_from_host.
 */
export function slugFromHost(hostname, base = baseDomain()) {
  if (!hostname || !base) return null
  const host = hostname.toLowerCase()
  if (host === base || !host.endsWith('.' + base)) return null
  const label = host.slice(0, host.length - base.length - 1)
  if (!label || label.includes('.')) return null          // solo el primer nivel es slug
  if (LABELS_RESERVADOS.has(label) || !SLUG_RE.test(label)) return null
  return label
}

/**
 * landingLoginUrlForHost — URL del login de la landing para rebotar una sesión ausente.
 *
 * CONTRATO con la landing (melquiadez.com/login) — la landing DEBE cumplirlo:
 *   - Este lado solo emite `next` derivado del host (slug que matchea ^[a-z0-9-]+$) o ningún `next`.
 *     Nunca se construye con input del usuario → CERO open redirect desde el dashboard.
 *   - Tras el login, la landing redirige SOLO a `https://{next}.melquiadez.com`: debe re-validar
 *     `next` contra ^[a-z0-9-]+$ y construir el host ella misma. JAMÁS tratar `next` como una URL ni
 *     redirigir a un host arbitrario.
 *
 * Devuelve null si no hay landing configurada (dev) → el caller usa el /login propio del dashboard.
 */
export function landingLoginUrlForHost(hostname = currentHostname()) {
  const origin = landingOrigin()
  if (!origin) return null
  const slug = slugFromHost(hostname, baseDomain())
  const base = `${origin.replace(/\/$/, '')}/login`
  return slug ? `${base}?next=${encodeURIComponent(slug)}` : base
}

export function currentHostname() {
  return typeof window !== 'undefined' ? window.location.hostname : ''
}

// Seam de navegación dura (aislado para poder espiarlo en tests, igual que `redirector` en api.js).
export const handoffNav = {
  toLanding(url) {
    if (typeof window !== 'undefined') window.location.replace(url)
  },
}
