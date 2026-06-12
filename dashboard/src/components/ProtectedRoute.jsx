/*
 * ProtectedRoute — exige sesión para el shell.
 *
 * Sin token:
 *   - En producción (landing configurada, plan §3): rebota a la página de login de la LANDING
 *     (`melquiadez.com/login`) con el slug DEL HOST actual como `next` — derivado del host, jamás de
 *     input del usuario (cero open redirect; ver lib/handoff.js para el contrato con la landing).
 *     `app.melquiadez.com` (sin slug) rebota sin `next`.
 *   - En dev (sin landing configurada): cae al /login propio del dashboard.
 * Con token: renderiza. La validez real la verifica el backend en la primera llamada (GET /config);
 * si está expirado, api.js intercepta el 401 y redirige a /login.
 */
import { Navigate } from 'react-router-dom'
import { getToken } from '@/lib/api.js'
import { landingLoginUrlForHost, handoffNav } from '@/lib/handoff.js'

export default function ProtectedRoute({ children }) {
  if (getToken()) return children
  // Rebote a la landing solo si está configurada (prod). landingLoginUrlForHost deriva el slug del host.
  const url = landingLoginUrlForHost()
  if (url) {
    handoffNav.toLanding(url)
    return null
  }
  return <Navigate to="/login" replace />
}
