/*
 * TenantManage — acciones por tenant del panel super-admin: toggle de features y enlace de set-password.
 *
 * - Toggle: PUT /admin/tenants/{slug}/features {feature, habilitada}. El backend valida catálogo +
 *   dependencias; ante 4xx se muestra el motivo. Al éxito, refresca (onCambio) para reflejar el set efectivo.
 * - Set-password: POST /admin/tenants/{slug}/identidad-admin {email} → muestra el enlace UNA vez para
 *   copiarlo. SEGURIDAD: el token NO se guarda (ni localStorage ni estado persistente); vive solo en el
 *   estado del componente hasta recargar.
 */
import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import { api } from '@/lib/api'
import { FEATURES_OPCIONALES } from './features.js'

export default function TenantManage({ tenant, onCambio }) {
  const activas = new Set(tenant.features || [])
  const [error, setError] = useState('')
  const [ocupada, setOcupada] = useState('')          // feature en curso (deshabilita su botón)
  const [email, setEmail] = useState('')
  const [emitiendo, setEmitiendo] = useState(false)
  const [enlace, setEnlace] = useState('')

  async function toggle(feature) {
    const habilitada = !activas.has(feature)
    setError('')
    setOcupada(feature)
    try {
      const res = await api(`/admin/tenants/${tenant.slug}/features`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feature, habilitada }),
      })
      if (res.ok) {
        onCambio?.()
      } else {
        const b = await res.json().catch(() => ({}))
        setError(b.detail || 'No se pudo cambiar la feature.')
      }
    } catch {
      setError('Error de conexión.')
    } finally {
      setOcupada('')
    }
  }

  async function generarEnlace(e) {
    e.preventDefault()
    if (!email.trim()) return
    setError('')
    setEnlace('')
    setEmitiendo(true)
    try {
      const res = await api(`/admin/tenants/${tenant.slug}/identidad-admin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      })
      if (res.ok) {
        const { set_password_token } = await res.json()
        const origin = typeof window !== 'undefined' ? window.location.origin : ''
        setEnlace(set_password_token ? `${origin}/set-password?token=${set_password_token}` : '')
        setEmail('')        // no conservar el email tras enviar
      } else {
        const b = await res.json().catch(() => ({}))
        setError(b.detail || 'No se pudo generar el enlace.')
      }
    } catch {
      setError('Error de conexión.')
    } finally {
      setEmitiendo(false)
    }
  }

  return (
    <Card className="p-4 flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-foreground">
        Gestionar <span className="text-primary">{tenant.slug}</span>
      </h2>

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] text-muted-foreground">Features (clic para activar/desactivar)</span>
        <div className="flex flex-wrap gap-1.5">
          {FEATURES_OPCIONALES.map(([f, label]) => {
            const on = activas.has(f)
            return (
              <button
                key={f} type="button" onClick={() => toggle(f)} disabled={ocupada === f}
                aria-label={`toggle ${f}`} aria-pressed={on}
                className={`text-[11px] px-2 py-1 rounded-sm border transition-colors disabled:opacity-50 ${
                  on ? 'bg-primary text-primary-foreground border-primary'
                     : 'bg-surface text-muted-foreground border-border hover:bg-surface-2'
                }`}
              >
                {ocupada === f && <Loader2 className="inline size-3 animate-spin mr-1" />}
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <form onSubmit={generarEnlace} className="flex flex-col gap-1.5" aria-label="Generar enlace de set-password">
        <span className="text-[11px] text-muted-foreground">Enlace de set-password del admin</span>
        <div className="flex gap-2">
          <Input
            type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            aria-label="Email del admin" placeholder="dueño@empresa.co" className="flex-1"
          />
          <Button type="submit" variant="outline" disabled={emitiendo}>
            {emitiendo && <Loader2 className="animate-spin" />}
            Generar enlace
          </Button>
        </div>
      </form>

      {enlace && (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-success">Enlace generado (cópialo ahora; no se vuelve a mostrar):</span>
          <Input readOnly value={enlace} aria-label="Enlace de set-password" onFocus={(e) => e.target.select()} />
        </div>
      )}

      {error && (
        <div role="alert" className="text-xs text-destructive bg-destructive/10 border border-destructive/40 rounded-md px-3 py-2">
          {error}
        </div>
      )}
    </Card>
  )
}
