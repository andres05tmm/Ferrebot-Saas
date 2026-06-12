/*
 * Login de la landing contra la API real (ADR 0009): POST /api/v1/auth/login/password.
 * Al éxito, el handoff al dashboard va por FRAGMENTO de URL (#token=...): no viaja al
 * servidor ni queda en logs. El dashboard lo lee al arrancar (rama de backend aparte).
 */

export const API_URL = import.meta.env.VITE_API_URL || 'https://app.melquiadez.com'
export const APP_URL = import.meta.env.VITE_APP_URL || 'https://app.melquiadez.com'

// Mensajes alineados con la API (401 genérico sin enumeración; 429 lockout).
export const MENSAJES = {
  credenciales: 'Email o contraseña incorrectos.',
  bloqueado: 'Demasiados intentos. Espera unos minutos y vuelve a intentar.',
  conexion: 'No pudimos conectar. Revisa tu internet e intenta de nuevo.',
}

/** URL del dashboard con el token en el fragmento (handoff sin servidor). */
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
