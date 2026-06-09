/*
 * PlatformRoute — gate del panel super-admin (ADR 0010 §D3).
 *
 * Solo una identidad de PLATAFORMA (rol super_admin) ve/accede /admin. Un usuario normal NO debe verlo:
 * sin sesión → /login; con sesión pero rol != super_admin → fuera (a su dashboard). El backend además
 * gatea cada /admin/* con require_platform; esto es el gate del cliente (ocultar la UI).
 */
import { Navigate } from 'react-router-dom'
import { getToken } from '@/lib/api.js'
import { useAuth } from '@/hooks/useAuth.js'

export default function PlatformRoute({ children }) {
  const { getUser } = useAuth()
  if (!getToken()) return <Navigate to="/login" replace />
  if (getUser()?.rol !== 'super_admin') return <Navigate to="/" replace />
  return children
}
