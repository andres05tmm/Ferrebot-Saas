/*
 * useAuth — utilidades de sesión del dashboard (adaptado del FerreBot original).
 *
 * El Bearer y el manejo de 401 viven en lib/api.js (no se duplican aquí). Este hook solo lee/escribe
 * la sesión en localStorage y expone `login` (POST /auth/login vía api.js). No usa hooks de React,
 * así que es seguro llamarlo fuera de un componente.
 */
import { api, TOKEN_KEY, USER_KEY, limpiarSesion, redirector } from '@/lib/api.js'

export function useAuth() {
  const getToken = () => {
    try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
  }

  const getUser = () => {
    try {
      const u = localStorage.getItem(USER_KEY)
      return u ? JSON.parse(u) : null
    } catch { return null }
  }

  const logout = () => {
    limpiarSesion()
    redirector.toLogin()
  }

  const isAdmin = () => getUser()?.rol === 'admin'

  /**
   * login — autentica el payload del Telegram Login Widget contra POST /auth/login.
   * En 200 guarda token y usuario {id, rol, tenant} y devuelve { ok: true }.
   * En 401/403 devuelve { ok: false, error } con un mensaje legible (sin lanzar).
   */
  const login = async (payloadTelegram) => {
    const res = await api('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payloadTelegram),
    })
    if (res.ok) {
      const data = await res.json()
      localStorage.setItem(TOKEN_KEY, data.token)
      localStorage.setItem(USER_KEY, JSON.stringify(data.usuario))
      return { ok: true, usuario: data.usuario }
    }
    if (res.status === 403) {
      return { ok: false, error: 'No tienes acceso. Pídele a Andrés que te registre.' }
    }
    if (res.status === 401) {
      return { ok: false, error: 'Error de verificación. Intenta de nuevo.' }
    }
    return { ok: false, error: 'Error al autenticar. Intenta de nuevo.' }
  }

  return { getToken, getUser, logout, isAdmin, login }
}
