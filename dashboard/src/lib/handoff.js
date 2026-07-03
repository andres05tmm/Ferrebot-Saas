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
 * Origins/dominios: en prod se DERIVAN del host actual en runtime (no del build), porque el build
 * desplegado no lleva las VITE_ vars (los .env.* están gitignored y no hay Dockerfile que las inyecte).
 * Las env VITE_LANDING_ORIGIN / VITE_BASE_DOMAIN, si están, ganan como override explícito. En dev local
 * (localhost / IP / un solo label) no se deriva base domain → no hay landing → el dashboard usa su /login.
 */
import { TOKEN_KEY, USER_KEY } from './api.js'

// Labels que NO son tenants — ESPEJA core/tenancy/resolver.py LABELS_RESERVADOS. `app.melquiadez.com`
// (entrada de clientes) no tiene slug, así que su rebote a la landing va SIN `next`.
const LABELS_RESERVADOS = new Set(['app', 'api', 'www', 'admin'])

// Contrato del slug acordado con la landing (ver landingLoginUrlForHost).
const SLUG_RE = /^[a-z0-9-]+$/

/**
 * baseDomain — dominio apex del despliegue. Override por env (VITE_BASE_DOMAIN); si no, se deriva del
 * host actual en runtime:
 *   - dev (localhost, *.localhost, una IP, o un solo label) → '' (no hay landing).
 *   - prod → el apex registrable = los dos últimos labels (barberia-demo.melquiadez.com y
 *     app.melquiadez.com → melquiadez.com; melquiadez.com → melquiadez.com).
 * NOTA: la heurística "dos últimos labels" no cubre hosts multinivel no estándar (p. ej. staging.x.y,
 * o TLDs compuestos tipo example.co.uk). Para esos casos, setear VITE_BASE_DOMAIN sigue siendo el camino.
 */
export function baseDomain(hostname = currentHostname()) {
  const env = import.meta.env.VITE_BASE_DOMAIN
  if (env) return env
  const host = (hostname || '').toLowerCase().trim()
  if (!host) return ''
  if (host === 'localhost' || host.endsWith('.localhost')) return ''   // dev
  if (isIpAddress(host)) return ''                                     // dev (IP literal)
  const labels = host.split('.')
  if (labels.length < 2) return ''                                     // un solo label → dev
  return labels.slice(-2).join('.')                                    // apex registrable
}

/**
 * landingOrigin — origin de la landing. Override por env (VITE_LANDING_ORIGIN); si no, en prod se deriva
 * como `https://${baseDomain()}`; en dev (sin base domain) → '' (no hay landing).
 */
export function landingOrigin(hostname = currentHostname()) {
  const env = import.meta.env.VITE_LANDING_ORIGIN
  if (env) return env
  const base = baseDomain(hostname)
  return base ? `https://${base}` : ''
}

// IP literal (IPv4 o IPv6) → host de dev, sin landing derivable.
function isIpAddress(host) {
  if (host.includes(':')) return true                  // IPv6
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host)           // IPv4
}

/**
 * consumeTokenFromHash — handoff de ENTRADA. Si la URL trae `#token=...`, guarda el token y limpia el
 * fragmento con history.replaceState (NO pushState → jamás entra al historial), preservando path+query.
 * Si ya había sesión, el token del fragmento la REEMPLAZA — incluida la identidad (`ferrebot_user`):
 * un usuario viejo no puede sobrevivir al token nuevo (el boot la rehidrata desde GET /config).
 * Devuelve true si consumió un token.
 *
 * Debe correr ANTES de montar el router/cualquier fetch (se llama en main.jsx, antes de createRoot).
 */
export function consumeTokenFromHash(win = window) {
  const hash = win.location?.hash || ''
  if (!hash.includes('token=')) return false
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const token = params.get('token')
  if (!token) return false
  try {
    win.localStorage.setItem(TOKEN_KEY, token)
    win.localStorage.removeItem(USER_KEY)   // la identidad de la sesión anterior no aplica al token nuevo
  } catch {}
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
  const origin = landingOrigin(hostname)
  if (!origin) return null
  const slug = slugFromHost(hostname, baseDomain(hostname))
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
