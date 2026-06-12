/*
 * AdminPanel — panel super-admin (ADR 0010 §D3). Vive FUERA del shell de tenant: el super-admin no tiene
 * empresa, así que no carga GET /config; opera cross-tenant contra /api/v1/admin. El gate de rol lo pone
 * PlatformRoute; el backend revalida cada ruta con require_platform.
 *
 * Compone: lista de empresas (GET /admin/tenants) + formulario de alta (con estado del job) + acciones
 * por tenant (toggle de features, enlace de set-password).
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth.js'
import { apiJson } from '@/lib/api.js'
import { Button } from '@/components/ui/button.jsx'
import { Card } from '@/components/ui/card.jsx'
import CrearTenantForm from './CrearTenantForm.jsx'
import TenantsTable from './TenantsTable.jsx'
import TenantManage from './TenantManage.jsx'

export default function AdminPanel() {
  const { logout } = useAuth()
  const [tenants, setTenants] = useState([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState('')
  const [seleccion, setSeleccion] = useState(null)

  const cargar = useCallback(async () => {
    setError('')
    try {
      const data = await apiJson('/admin/tenants')
      setTenants(Array.isArray(data) ? data : [])
      setSeleccion((prev) => (prev ? data.find((t) => t.slug === prev.slug) || null : null))
    } catch {
      setError('No se pudieron cargar las empresas.')
    } finally {
      setCargando(false)
    }
  }, [])

  useEffect(() => { cargar() }, [cargar])

  return (
    <main className="min-h-[100dvh] bg-background text-foreground p-5 flex flex-col gap-4 max-w-5xl mx-auto">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-extrabold tracking-tight">Panel super-admin</h1>
          <p className="text-[11px] text-muted-foreground uppercase tracking-wider">Melquiadez · plataforma</p>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>Salir</Button>
      </header>

      <CrearTenantForm onProvisionado={cargar} />

      <Card className="p-4 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Empresas</h2>
          <Button variant="ghost" size="sm" onClick={cargar}>Refrescar</Button>
        </div>
        {cargando ? (
          <div className="py-8 grid place-items-center" role="status" aria-label="Cargando empresas">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div role="alert" className="text-xs text-destructive bg-destructive/10 border border-destructive/40 rounded-md px-3 py-2">
            {error}
          </div>
        ) : (
          <TenantsTable tenants={tenants} onSelect={setSeleccion} seleccionado={seleccion?.slug} />
        )}
      </Card>

      {seleccion && <TenantManage tenant={seleccion} onCambio={cargar} />}
    </main>
  )
}
