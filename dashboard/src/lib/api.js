/*
 * api.js — wrapper único de fetch del dashboard.
 *
 * Base /api/v1 (el backend SaaS expone los endpoints bajo ese prefijo). En DESARROLLO añade el
 * header X-Tenant-Slug (VITE_TENANT_SLUG) para resolver la empresa sin subdominio; en PRODUCCIÓN
 * la empresa la da el subdominio y NO se manda el header.
 *
 * Punto de extensión (E4 — auth): cuando exista sesión, aquí se añadirá
 * `Authorization: Bearer <token>` antes de delegar en fetch.
 */
const BASE = '/api/v1'

function esDev() {
  const dev = import.meta.env.DEV
  return dev === true || dev === 'true'
}

export function api(path, options = {}) {
  const headers = new Headers(options.headers || {})
  // TODO(E4): si hay token de sesión → headers.set('Authorization', `Bearer ${token}`)
  if (esDev()) {
    const slug = import.meta.env.VITE_TENANT_SLUG
    if (slug) headers.set('X-Tenant-Slug', slug)
  }
  return fetch(`${BASE}${path}`, { ...options, headers })
}

export async function apiJson(path, options = {}) {
  const res = await api(path, options)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
