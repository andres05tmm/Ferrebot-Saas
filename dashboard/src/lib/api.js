/*
 * api.js — wrapper único de fetch del dashboard (auth + tenant centralizados).
 *
 * Base /api/v1. Añade Authorization: Bearer <token de localStorage> si hay sesión, y en DESARROLLO
 * el header X-Tenant-Slug (VITE_TENANT_SLUG) para resolver la empresa sin subdominio (en prod la da
 * el subdominio). Ante un 401 limpia la sesión y rebota al login: en PROD a la landing
 * (`melquiadez.com/login?next={slug del host}` — el login es único y vive allí, plan §3); en DEV
 * (sin landing) al /login propio del dashboard. Nunca redirige si ya estás en /login (evita el bucle:
 * el propio POST /auth/login responde 401 en credenciales inválidas).
 */
import { landingLoginUrlForHost } from './handoff.js'

const BASE = '/api/v1'
export const TOKEN_KEY = 'ferrebot_token'
export const USER_KEY = 'ferrebot_user'

function esDev() {
  const dev = import.meta.env.DEV
  return dev === true || dev === 'true'
}

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function limpiarSesion() {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {}
}

// Seam de navegación: aislado en un objeto para poder espiarlo en tests (window.location en jsdom
// es unforgeable). Lo usan tanto el intercept de 401 como useAuth.logout.
//   - toLogin: navegación blanda al /login propio (DEV, sin landing).
//   - toLanding: navegación dura (replace, fuera del historial) a la landing externa (PROD).
export const redirector = {
  toLogin() {
    if (typeof window !== 'undefined') window.location.href = '/login'
  },
  toLanding(url) {
    if (typeof window !== 'undefined') window.location.replace(url)
  },
}

// Rebote por sesión ausente/expirada (401). En prod va a la landing (login único); en dev al /login
// propio. Anti-bucle: si ya estamos en /login no redirige (el form muestra el error de credenciales).
function redirigirSinSesion() {
  if (typeof window === 'undefined') return
  if (window.location?.pathname === '/login') return
  const landingUrl = landingLoginUrlForHost()
  if (landingUrl) redirector.toLanding(landingUrl)
  else redirector.toLogin()
}

// Única fuente de verdad de auth + tenant: Bearer (si hay sesión) y X-Tenant-Slug (solo en dev).
// La usan api() y el stream SSE (useRealtime), que necesita los MISMOS headers (fetch-based).
export function buildAuthHeaders() {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (esDev()) {
    const slug = import.meta.env.VITE_TENANT_SLUG
    if (slug) headers['X-Tenant-Slug'] = slug
  }
  return headers
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  for (const [clave, valor] of Object.entries(buildAuthHeaders())) headers.set(clave, valor)

  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  if (res.status === 401) {
    limpiarSesion()
    redirigirSinSesion()
  }
  return res
}

export async function apiJson(path, options = {}) {
  const res = await api(path, options)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
