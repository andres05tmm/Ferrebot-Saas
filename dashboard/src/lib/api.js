/*
 * api.js — wrapper único de fetch del dashboard (auth + tenant centralizados).
 *
 * Base /api/v1. Añade Authorization: Bearer <token de localStorage> si hay sesión, y en DESARROLLO
 * el header X-Tenant-Slug (VITE_TENANT_SLUG) para resolver la empresa sin subdominio (en prod la da
 * el subdominio). Ante un 401 limpia la sesión y redirige a /login — salvo que ya estés en /login
 * (evita el bucle: el propio POST /auth/login responde 401 en credenciales inválidas).
 */
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
export const redirector = {
  toLogin() {
    if (typeof window !== 'undefined') window.location.href = '/login'
  },
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
    // No redirigir si ya estamos en /login (el error del login se muestra en el formulario).
    if (typeof window !== 'undefined' && window.location?.pathname !== '/login') {
      redirector.toLogin()
    }
  }
  return res
}

export async function apiJson(path, options = {}) {
  const res = await api(path, options)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
