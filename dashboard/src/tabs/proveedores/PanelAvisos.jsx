/*
 * PanelAvisos — lo que traía el tab "Cuentas por pagar": las cuentas clasificadas por vencimiento y
 * las reglas del motor que le avisa al DUEÑO (pack `pagar`, ADR 0019).
 *
 * Vive plegado al final del tab de proveedores: el día a día es el estado de cada proveedor; esto es
 * la vigilancia del calendario de pagos y su configuración, que se toca de vez en cuando.
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, CalendarClock, ChevronDown } from 'lucide-react'
import { api } from '@/lib/api'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', {
    day: '2-digit', month: 'short', timeZone: 'America/Bogota',
  })
}

function vencimientoTexto(c) {
  if (c.dias_para_vencer < 0) {
    const n = Math.abs(c.dias_para_vencer)
    return `vencida hace ${n} día${n === 1 ? '' : 's'}`
  }
  if (c.dias_para_vencer === 0) return 'vence hoy'
  return `vence en ${c.dias_para_vencer} día${c.dias_para_vencer === 1 ? '' : 's'}`
}

export default function PanelAvisos() {
  const [abierto, setAbierto] = useState(false)
  const conPack = useFeatures().includes('pack_pagar')
  const cuentasQ = useFetch(conPack ? '/pagar/cuentas' : null)
  const configQ = useFetch(conPack && abierto ? '/pagar/config' : null)
  useRealtimeEvent(['pagar_aviso'], cuentasQ.refetch)

  if (!conPack) return null   // el motor de avisos es el pack `pagar`; sin él no hay calendario

  const cuentas = arr(cuentasQ.data)
  const vencidas = cuentas.filter(c => c.vencida)
  const porVencer = cuentas.filter(c => !c.vencida)

  return (
    <Card className="p-3.5">
      <button onClick={() => setAbierto(a => !a)} aria-expanded={abierto}
        className="w-full flex items-center gap-2 text-left">
        <CalendarClock className="size-4 text-primary" />
        <span className="text-body-sm font-semibold flex-1">Calendario de pagos</span>
        {vencidas.length > 0 && (
          <span className="text-caption text-danger">{vencidas.length} vencida(s)</span>
        )}
        <ChevronDown className={`size-4 text-muted-foreground transition-transform ${abierto ? 'rotate-180' : ''}`} />
      </button>

      {abierto && (
        <div className="mt-3 space-y-3">
          <Seccion titulo="Vencidas" icon={AlertTriangle} tono="text-danger" cuentas={vencidas}
            vacio="Nada vencido. 🎉" />
          <Seccion titulo="Por vencer" icon={CalendarClock} tono="text-warning" cuentas={porVencer}
            vacio="Nada por vencer en la ventana." />
          <Reglas config={configQ.data} refetch={configQ.refetch} />
        </div>
      )}
    </Card>
  )
}

function Seccion({ titulo, icon: Icon, tono, cuentas, vacio }) {
  return (
    <div>
      <h3 className="text-body-sm font-semibold mb-1.5 inline-flex items-center gap-1.5">
        <Icon className={`size-4 ${tono}`} /> {titulo}
        {cuentas.length > 0 && <span className="text-caption text-muted-foreground">({cuentas.length})</span>}
      </h3>
      {cuentas.length === 0 ? (
        <p className="py-3 text-center text-body-sm text-muted-foreground">{vacio}</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {cuentas.map(c => (
            <li key={c.factura_id} className="py-2 flex items-center gap-3 text-body-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate">{c.proveedor}</div>
                <div className="text-caption text-muted-foreground">
                  #{c.factura_id} · {fechaCorta(c.vencimiento_efectivo)} · {vencimientoTexto(c)}
                </div>
              </div>
              <span className="tabular-nums font-medium">{cop(c.pendiente)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Reglas({ config, refetch }) {
  const [f, setF] = useState(null)
  useEffect(() => { if (config && !f) setF(config) }, [config])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!f) return null
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    try {
      const res = await api('/pagar/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          activo: !!f.activo,
          dias_aviso_previo: Number(f.dias_aviso_previo) || 0,
          cadencia_dias: Number(f.cadencia_dias) || 1,
          hora_inicio: f.hora_inicio, hora_fin: f.hora_fin,
          plazo_default_dias: Number(f.plazo_default_dias) || 30,
        }),
      })
      if (res.ok) { toast.success('Reglas guardadas'); refetch?.() }
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') }
  }

  const campo = (label, k, type = 'number') => (
    <label className="flex flex-col gap-1">
      <span className="text-caption uppercase tracking-wider text-muted-foreground">{label}</span>
      <Input type={type} value={f[k] ?? ''} onChange={set(k)} aria-label={label} className="h-9" />
    </label>
  )

  return (
    <div className="pt-3 border-t border-border-subtle">
      <h3 className="text-body-sm font-semibold mb-2">Cuándo avisarme</h3>
      <div className="grid grid-cols-2 gap-2.5">
        {campo('Aviso previo (días)', 'dias_aviso_previo')}
        {campo('Cadencia (días)', 'cadencia_dias')}
        {campo('Desde (hora)', 'hora_inicio', 'time')}
        {campo('Hasta (hora)', 'hora_fin', 'time')}
        {campo('Plazo por defecto (días)', 'plazo_default_dias')}
        <label className="inline-flex items-center gap-2 text-body-sm self-end pb-2">
          <input type="checkbox" checked={!!f.activo} aria-label="Avisos activos"
            onChange={(e) => setF(p => ({ ...p, activo: e.target.checked }))} />
          Avisos activos
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button size="sm" onClick={guardar}>Guardar reglas</Button>
      </div>
      <p className="mt-2 text-caption text-muted-foreground">
        El aviso es interno (te llega a ti, no al proveedor). Una factura sin fecha de vencimiento se
        considera con el plazo por defecto.
      </p>
    </div>
  )
}
