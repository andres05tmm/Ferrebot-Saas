/*
 * TabStub — placeholder de tab (andamiaje E3). La ruta existe; el cuerpo real se porta en E6.
 */
import { useLocation } from 'react-router-dom'
import { findRoute } from '@/routes.jsx'

export default function TabStub() {
  const { pathname } = useLocation()
  const label = findRoute(pathname)?.label || 'Vista'
  return (
    <div className="grid place-items-center min-h-[60vh] text-center">
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-foreground">{label}</h2>
        <p className="text-sm text-muted-foreground">Próximamente</p>
      </div>
    </div>
  )
}
