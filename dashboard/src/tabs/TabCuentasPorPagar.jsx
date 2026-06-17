/*
 * TabCuentasPorPagar — página del pack pagar (ADR 0019): las cuentas por pagar a proveedores que el
 * agente vigila para avisarle al DUEÑO. Espejo de TabCartera, pero el aviso es INTERNO (al dueño, no a
 * un tercero): por eso no hay opt-out ni promesas, solo la lista clasificada (vencidas / por vencer) y
 * la configuración del motor (ventana, cadencia, aviso previo, plazo por defecto).
 * Gateada por la feature 'pack_pagar' (la ruta se oculta sin ella). TODO el backend de /pagar es de
 * admin (saldos con proveedores = dato sensible), así que la pestaña entera se gatea por rol aquí.
 * Tiempo real: refetch ante el evento interno 'pagar_aviso' (el cron acaba de avisar al dueño).
 */
import { Banknote, CalendarClock, AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api.js'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

// El cron interno avisa al dueño y publica este evento (SSE): la lista por pagar pudo cambiar.
const EVENTOS = ['pagar_aviso']

async function enviar(path, method, body, okMsg, after) {
  try {
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) { toast.success(okMsg); after?.(); return true }
    if (res.status === 403) toast.error('Necesitas permisos de administrador')
    else toast.error('No se pudo guardar')
  } catch { toast.error('Error de conexión') }
  return false
}

function fechaCorta(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

// Texto humano del vencimiento: "vence en N días" / "vence hoy" / "vencida hace N días".
function vencimientoTexto(c) {
  if (c.dias_para_vencer < 0) {
    const n = Math.abs(c.dias_para_vencer)
    return `vencida hace ${n} día${n === 1 ? '' : 's'}`
  }
  if (c.dias_para_vencer === 0) return 'vence hoy'
  return `vence en ${c.dias_para_vencer} día${c.dias_para_vencer === 1 ? '' : 's'}`
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

function SeccionCuentas({ titulo, icon: Icon, tono, cuentas, vacio }) {
  return (
    <Card className="p-3">
      <h2 className="text-sm font-semibold mb-2 inline-flex items-center gap-1.5">
        <Icon className={`size-4 ${tono}`} /> {titulo}
        {cuentas.length > 0 && (
          <span className="text-[11px] text-muted-foreground">({cuentas.length})</span>
        )}
      </h2>
      {cuentas.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{vacio}</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {cuentas.map(c => (
            <li key={c.factura_id} className="py-2.5 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-[13px] truncate">{c.proveedor}</div>
                <div className="text-[12px] text-muted-foreground">
                  #{c.factura_id} · {fechaCorta(c.vencimiento_efectivo)} · {vencimientoTexto(c)}
                </div>
              </div>
              <div className="font-semibold tabular-nums text-[13px]">{cop(c.pendiente)}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function SeccionConfig({ config, refetch }) {
  const [f, setF] = useState(null)
  useEffect(() => { if (config && !f) setF(config) }, [config]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!f) return null
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    const body = {
      activo: !!f.activo,
      dias_aviso_previo: Number(f.dias_aviso_previo) || 0,
      cadencia_dias: Number(f.cadencia_dias) || 1,
      hora_inicio: f.hora_inicio,
      hora_fin: f.hora_fin,
      plazo_default_dias: Number(f.plazo_default_dias) || 30,
    }
    await enviar('/pagar/config', 'PUT', body, 'Configuración guardada', refetch)
  }

  const campo = (label, k, type = 'number') => (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <Input type={type} value={f[k] ?? ''} onChange={set(k)} aria-label={label} className="h-9" />
    </label>
  )

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Reglas de aviso</h3>
      <div className="grid grid-cols-2 gap-2.5">
        {campo('Aviso previo (días)', 'dias_aviso_previo')}
        {campo('Cadencia (días)', 'cadencia_dias')}
        {campo('Desde (hora)', 'hora_inicio', 'time')}
        {campo('Hasta (hora)', 'hora_fin', 'time')}
        {campo('Plazo por defecto (días)', 'plazo_default_dias')}
        <label className="inline-flex items-center gap-2 text-sm self-end pb-2">
          <input type="checkbox" checked={!!f.activo} aria-label="Avisos activos"
            onChange={e => setF(p => ({ ...p, activo: e.target.checked }))} />
          Avisos activos
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button onClick={guardar}>Guardar reglas</Button>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Es un aviso INTERNO para ti (no le llega a tu proveedor). Solo se emite dentro de la ventana
        horaria; el "aviso previo" marca cuántos días antes del vencimiento empezar, y la "cadencia"
        evita repetir el mismo aviso. Sin fecha de vencimiento, se asume el plazo por defecto.
      </p>
    </Card>
  )
}

export default function TabCuentasPorPagar() {
  const { isAdmin } = useAuth()
  // El backend de /pagar es 100% admin (403 para staff): sin rol, ni siquiera se consulta.
  if (!isAdmin()) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        Las cuentas por pagar son información sensible: solo un administrador puede verlas.
      </Card>
    )
  }
  return <CuentasPorPagarAdmin />
}

function CuentasPorPagarAdmin() {
  const cuentasQ = useFetch('/pagar/cuentas')
  const configQ = useFetch('/pagar/config')

  useRealtimeEvent(EVENTOS, () => { cuentasQ.refetch() })

  const cuentas = arr(cuentasQ.data)
  const vencidas = cuentas.filter(c => c.vencida)
  const porVencer = cuentas.filter(c => c.por_vencer)
  const totalVencido = vencidas.reduce((acc, c) => acc + Number(c.pendiente || 0), 0)
  const totalPorVencer = porVencer.reduce((acc, c) => acc + Number(c.pendiente || 0), 0)

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <Banknote className="size-4.5 text-primary" /> Cuentas por pagar
      </h1>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="Vencido" value={cuentasQ.loading ? '…' : cop(totalVencido)}
          hint={`${vencidas.length} factura${vencidas.length === 1 ? '' : 's'}`} />
        <Kpi label="Por vencer" value={cuentasQ.loading ? '…' : cop(totalPorVencer)}
          hint={`${porVencer.length} factura${porVencer.length === 1 ? '' : 's'}`} />
        <Kpi label="Cuentas vencidas" value={cuentasQ.loading ? '…' : vencidas.length} />
        <Kpi label="Por vencer pronto" value={cuentasQ.loading ? '…' : porVencer.length} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div className="space-y-3">
          <SeccionCuentas titulo="Vencidas" icon={AlertTriangle} tono="text-destructive"
            cuentas={vencidas} vacio="Nada vencido — al día con tus proveedores. 🎉" />
          <SeccionCuentas titulo="Por vencer" icon={CalendarClock} tono="text-primary"
            cuentas={porVencer} vacio="Nada por vencer en la ventana de aviso." />
        </div>
        <SeccionConfig config={configQ.data} refetch={configQ.refetch} />
      </div>
    </div>
  )
}
