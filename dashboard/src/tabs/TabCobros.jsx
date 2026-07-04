/*
 * TabCobros — frente de pagos/cobros Bold (ADR 0013). Gateada por la feature 'pagos_online'.
 * El backend (/pagos/cobros) deja LEER a staff (ver qué está pendiente al despachar) y CERRAR a mano
 * solo a admin (pagado-manual / cancelar mueven la verdad del dinero). Aquí: staff ve la lista y los
 * KPIs; el admin además marca pagado / cancela los cobros 'pendiente'. Filtro por estado.
 * Tiempo real: refetch ante cobro_creado / cobro_pagado / cobro_estado.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { CreditCard, ExternalLink, CheckCircle2, XCircle } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useCobros, useAccionCobro, keyPrefix } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const EVENTOS = ['cobro_creado', 'cobro_pagado', 'cobro_estado']

// Filtros de estado ofrecidos (además de "todos"). Solo 'pendiente' es mutable en el backend.
const FILTROS = [
  { id: '', label: 'Todos' },
  { id: 'pendiente', label: 'Pendientes' },
  { id: 'pagado', label: 'Pagados' },
  { id: 'vencido', label: 'Vencidos' },
  { id: 'cancelado', label: 'Cancelados' },
]

const ESTADO_BADGE = {
  pendiente: 'bg-info/10 text-info border-info/20',
  pagado: 'bg-success/10 text-success border-success/20',
  vencido: 'bg-warning/10 text-warning border-warning/20',
  cancelado: 'bg-muted text-muted-foreground border-border',
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

function Kpi({ label, value, hint }) {
  return (
    <Card className="p-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </Card>
  )
}

export default function TabCobros() {
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const [filtro, setFiltro] = useState('')
  const qc = useQueryClient()
  const cobrosQ = useCobros(filtro)
  const accionM = useAccionCobro()
  useRealtimeEvent(EVENTOS, () => qc.invalidateQueries({ queryKey: keyPrefix.cobros }))

  const cobros = arr(cobrosQ.data)
  const { pendientes, montoPendiente, pagados } = useMemo(() => {
    // Los KPIs son del set cargado; para que sean estables se calculan sobre "todos" solo si el filtro
    // está vacío. Con filtro activo reflejan lo visible (etiquetado en el hint).
    const pend = cobros.filter(c => c.estado === 'pendiente')
    return {
      pendientes: pend.length,
      montoPendiente: pend.reduce((acc, c) => acc + Number(c.monto || 0), 0),
      pagados: cobros.filter(c => c.estado === 'pagado').length,
    }
  }, [cobros])

  async function accion(cobro, tipo) {
    try {
      const res = await accionM.mutateAsync({ id: cobro.id, tipo })
      if (res.ok) {
        toast.success(tipo === 'pagar' ? 'Cobro marcado como pagado' : 'Cobro cancelado')
      } else if (res.status === 409) {
        toast.error('El cobro ya no está pendiente')
      } else if (res.status === 403) {
        toast.error('Necesitas permisos de administrador')
      } else {
        toast.error('No se pudo actualizar el cobro')
      }
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <CreditCard className="size-4.5 text-primary" /> Cobros
      </h1>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Kpi label="Por cobrar" value={cobrosQ.isLoading ? '…' : cop(montoPendiente)}
          hint={`${pendientes} pendiente${pendientes === 1 ? '' : 's'}`} />
        <Kpi label="Pendientes" value={cobrosQ.isLoading ? '…' : pendientes} />
        <Kpi label="Pagados" value={cobrosQ.isLoading ? '…' : pagados} hint="en la vista actual" />
      </div>

      <div className="flex flex-wrap gap-1.5">
        {FILTROS.map(f => (
          <button key={f.id || 'todos'} onClick={() => setFiltro(f.id)}
            className={`text-[12px] px-2.5 h-8 rounded-md border transition-colors ${
              filtro === f.id ? 'bg-primary text-primary-foreground border-primary' : 'bg-surface border-border hover:bg-surface-2'
            }`}>
            {f.label}
          </button>
        ))}
      </div>

      <Card className="p-0 overflow-hidden">
        {cobrosQ.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : cobrosQ.isError ? (
          <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar los cobros.</p>
        ) : cobros.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Sin cobros {filtro ? `en estado "${filtro}"` : 'todavía'}.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {cobros.map(c => (
              <li key={c.id} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.descripcion || c.referencia}</div>
                  <div className="text-[11px] text-muted-foreground tabular-nums">
                    {c.cliente_telefono || 'sin teléfono'} · {c.proveedor || 'manual'} · {fechaCorta(c.creado_en)}
                    {c.url && (
                      <a href={c.url} target="_blank" rel="noopener noreferrer"
                        className="ml-1.5 inline-flex items-center gap-0.5 text-primary hover:underline">
                        link <ExternalLink className="size-3" />
                      </a>
                    )}
                  </div>
                </div>
                <span className="tabular-nums font-semibold shrink-0">{cop(c.monto)}</span>
                <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[c.estado] || ''}`}>
                  {c.estado}
                </Badge>
                {admin && c.estado === 'pendiente' && (
                  <div className="flex gap-1 shrink-0">
                    <Button size="sm" variant="ghost" className="h-7 px-2 text-success"
                      aria-label={`Marcar pagado el cobro ${c.id}`} title="Marcar pagado"
                      onClick={() => accion(c, 'pagar')}>
                      <CheckCircle2 className="size-4" />
                    </Button>
                    <Button size="sm" variant="ghost" className="h-7 px-2 text-destructive"
                      aria-label={`Cancelar el cobro ${c.id}`} title="Cancelar"
                      onClick={() => accion(c, 'cancelar')}>
                      <XCircle className="size-4" />
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {!admin && (
        <p className="text-[11px] text-muted-foreground px-1">
          Cerrar un cobro a mano (marcar pagado / cancelar) es solo para administradores.
        </p>
      )}
    </div>
  )
}
