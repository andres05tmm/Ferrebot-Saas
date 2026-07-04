/*
 * TabConciliacion — conciliación bancaria (ADR 0028). Gateada por 'conciliacion_bancaria', SOLO admin.
 * Cruza los movimientos del extracto con ventas/gastos/abonos internos. El match automático (POST
 * /bancos/sugerir) marca 'sugerido' SOLO los de candidato único; los AMBIGUOS (varios candidatos) exigen
 * que un humano elija explícitamente antes de conciliar (POST /bancos/movimientos/{id}/conciliar). Nunca
 * concilia solo un ambiguo. GET /bancos/movimientos?estado. Enlazar no toca saldos: solo cruza.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { Landmark, Wand2, ArrowDownLeft, ArrowUpRight, Link2, CheckCircle2 } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useMovimientosBancarios, useSugerirConciliacion, useConciliar, keyPrefix } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

const FILTROS = [
  { id: '', label: 'Todos' },
  { id: 'no_conciliado', label: 'Sin conciliar' },
  { id: 'sugerido', label: 'Sugeridos' },
  { id: 'conciliado', label: 'Conciliados' },
]

const ESTADO_BADGE = {
  no_conciliado: 'bg-muted text-muted-foreground border-border',
  sugerido: 'bg-warning/10 text-warning border-warning/20',
  conciliado: 'bg-success/10 text-success border-success/20',
}
const ESTADO_LABEL = { no_conciliado: 'sin conciliar', sugerido: 'sugerido', conciliado: 'conciliado' }

function fechaCorta(f) {
  if (!f) return '—'
  return new Date(f).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

function Movimiento({ item, onConciliar }) {
  const m = item.movimiento
  const candidatos = arr(item.candidatos)
  const credito = m.naturaleza === 'credito'
  const ambiguo = candidatos.length > 1
  return (
    <li className="px-3.5 py-2.5 space-y-2 text-[13px]">
      <div className="flex items-center gap-3">
        {credito ? <ArrowDownLeft className="size-4 text-success shrink-0" /> : <ArrowUpRight className="size-4 text-destructive shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{m.referencia_bancaria || 'movimiento'}</div>
          <div className="text-[11px] text-muted-foreground">{fechaCorta(m.fecha)} · {credito ? 'entrada' : 'salida'}</div>
        </div>
        <span className={`tabular-nums font-semibold shrink-0 ${credito ? 'text-success' : 'text-destructive'}`}>{cop(m.monto)}</span>
        <Badge variant="outline" className={`h-5 text-[10px] shrink-0 ${ESTADO_BADGE[m.estado_conciliacion] || ''}`}>
          {ESTADO_LABEL[m.estado_conciliacion] || m.estado_conciliacion}
        </Badge>
      </div>

      {m.estado_conciliacion === 'conciliado' ? (
        <div className="ml-7 text-[11px] text-success inline-flex items-center gap-1">
          <CheckCircle2 className="size-3.5" /> enlazado con {m.conciliado_con_tipo} #{m.conciliado_con_id}
        </div>
      ) : candidatos.length === 0 ? (
        <div className="ml-7 text-[11px] text-muted-foreground">Sin candidatos internos que calcen.</div>
      ) : (
        <div className="ml-7 space-y-1.5">
          {ambiguo && (
            <div className="text-[11px] text-warning">Varios candidatos: elige cuál corresponde (no se concilia solo).</div>
          )}
          {candidatos.map(cand => (
            <div key={`${cand.tipo}-${cand.id}`} className="flex items-center gap-2">
              <span className="text-[12px] text-muted-foreground flex-1 truncate">
                {cand.tipo} #{cand.id} · {fechaCorta(cand.fecha)} · {cop(cand.monto)}
                {cand.descripcion ? ` · ${cand.descripcion}` : ''}
              </span>
              <Button size="sm" variant="ghost" className="h-7 px-2 text-primary shrink-0"
                aria-label={`Conciliar ${m.id} con ${cand.tipo} ${cand.id}`}
                onClick={() => onConciliar(m.id, cand)}>
                <Link2 className="size-3.5 mr-1" /> Conciliar
              </Button>
            </div>
          ))}
        </div>
      )}
    </li>
  )
}

export default function TabConciliacion() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        La conciliación bancaria es solo para administradores.
      </Card>
    )
  }
  return <ConciliacionContenido />
}

function ConciliacionContenido() {
  const [filtro, setFiltro] = useState('')
  const [sugiriendo, setSugiriendo] = useState(false)
  const qc = useQueryClient()
  const movsQ = useMovimientosBancarios(filtro)
  const sugerirM = useSugerirConciliacion()
  const conciliarM = useConciliar()
  useRealtimeEvent(['reconnected'], () => qc.invalidateQueries({ queryKey: keyPrefix.bancosMovimientos }))

  const movimientos = arr(movsQ.data)

  async function correrSugerencias() {
    setSugiriendo(true)
    try {
      const res = await sugerirM.mutateAsync()
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.success(`${data.sugeridos ?? 0} movimiento(s) sugerido(s)`)
      } else toast.error('No se pudo correr el match')
    } catch { toast.error('Error de conexión') } finally { setSugiriendo(false) }
  }

  async function conciliar(movId, cand) {
    try {
      const res = await conciliarM.mutateAsync({ movId, tipo: cand.tipo, idInterno: cand.id })
      if (res.ok) toast.success('Movimiento conciliado')
      else if (res.status === 422) toast.error('El enlace no es válido (monto o naturaleza no calzan)')
      else if (res.status === 404) toast.error('El movimiento ya no existe')
      else toast.error('No se pudo conciliar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <Landmark className="size-4.5 text-primary" /> Conciliación bancaria
        </h1>
        <Button size="sm" className="ml-auto inline-flex items-center gap-1.5" disabled={sugiriendo}
          onClick={correrSugerencias}>
          <Wand2 className="size-4" /> {sugiriendo ? 'Cruzando…' : 'Correr sugerencias'}
        </Button>
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
        {movsQ.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : movsQ.isError ? (
          <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar los movimientos.</p>
        ) : movimientos.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {filtro ? 'Sin movimientos en ese estado.' : 'Sin movimientos bancarios ingeridos todavía.'}
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {movimientos.map(item => (
              <Movimiento key={item.movimiento.id} item={item} onConciliar={conciliar} />
            ))}
          </ul>
        )}
      </Card>

      <p className="text-[11px] text-muted-foreground px-1">
        El cruce automático solo sugiere los movimientos con un único candidato. Los ambiguos requieren
        que elijas la contraparte a mano. Conciliar solo enlaza el movimiento: no mueve saldos.
      </p>
    </div>
  )
}
