/*
 * useAuth — utilidades de sesión del dashboard (adaptado del FerreBot original).
 *
 * El Bearer y el manejo de 401 viven en lib/api.js (no se duplican aquí). Este hook solo lee/escribe
 * la sesión en localStorage y expone `loginConPassword` (POST /auth/login/password vía api.js).
 * No usa hooks de React, así que es seguro llamarlo fuera de un componente.
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
   * loginConPassword — login real email/contraseña (ADR 0009) contra POST /auth/login/password.
   * En 200 guarda token + usuario y devuelve { ok: true }. Mensajes SIN enumeración: el mismo texto
   * para email inexistente y clave errada (401); 429 = bloqueado por intentos. No lanza.
   */
  const loginConPassword = async (email, password) => {
    const res = await api('/auth/login/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (res.ok) {
      const data = await res.json()
      localStorage.setItem(TOKEN_KEY, data.token)
      localStorage.setItem(USER_KEY, JSON.stringify(data.usuario))
      return { ok: true, usuario: data.usuario }
    }
    if (res.status === 429) {
      return { ok: false, error: 'Demasiados intentos. Espera unos minutos e inténtalo de nuevo.' }
    }
    if (res.status === 401) {
      return { ok: false, error: 'Email o contraseña incorrectos.' }
    }
    return { ok: false, error: 'No pudimos iniciar sesión. Intenta de nuevo.' }
  }

  return { getToken, getUser, logout, isAdmin, loginConPassword }
}
