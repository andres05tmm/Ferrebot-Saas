/*
 * ProtectedRoute — exige sesión para el shell. Sin token → /login; con token → renderiza.
 * La validez real del token la verifica el backend en la primera llamada (GET /config); si está
 * expirado, api.js intercepta el 401 y redirige a /login.
 */
import { Navigate } from 'react-router-dom'
import { getToken } from '@/lib/api.js'

export default function ProtectedRoute({ children }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return children
}
