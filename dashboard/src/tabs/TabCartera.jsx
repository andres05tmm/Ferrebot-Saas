/*
 * TabCartera — página del pack cobranza (ADR 0015): la cartera del negocio cobrada por el agente.
 * Gateada por la feature 'pack_cobranza' (la ruta se oculta sin ella). TODO el backend de /cobranza
 * es de admin (la cartera es dato sensible del cliente final), así que la pestaña entera se gatea
 * por rol aquí también. Muestra: KPIs (total en cartera, deudores, promesas vigentes, pagos por
 * verificar), tabla de deudores (saldo, recordatorios, promesa, opt-out), bandeja de pagos
 * reportados (verificar) y la configuración del motor (cadencia, tope, ventana, activo).
 * Tiempo real: refetch ante eventos de cobranza y de fiados (un abono cambia la cartera).
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { BellOff, BellRing, HandCoins, Inbox, Users } from 'lucide-react'
import { api } from '@/lib/api'
import { cop, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import CarteraAlquilerSection from './construccion/CarteraAlquiler.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

// Eventos que mueven la cartera: los del pack + los de fiados (un abono baja el saldo).
const EVENTOS = [
  'promesa_registrada', 'pago_reportado', 'pago_verificado', 'cobranza_opt_out',
  'fiado_registrado', 'fiado_abonado',
]

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

function Kpi({ label, value, hint }) {
  return (
    <Card className="p-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground">{hint}</div>}
    </Card>
  )
}

function SeccionDeudores({ deudores, onOptOut }) {
  return (
    <Card className="p-3">
      <h2 className="text-sm font-semibold mb-2 inline-flex items-center gap-1.5">
        <Users className="size-4 text-primary" /> Deudores
      </h2>
      {deudores.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          Sin cartera pendiente — todos los clientes están al día. 🎉
        </p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {deudores.map(d => (
            <li key={d.cliente_id} className={`py-2.5 flex items-center gap-3 ${d.opt_out ? 'opacity-60' : ''}`}>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-[13px] truncate">
                  {d.nombre}
                  {d.opt_out && <span className="ml-1.5 text-[11px] text-muted-foreground">(sin recordatorios)</span>}
                </div>
                <div className="text-[12px] text-muted-foreground">
                  {d.telefono || 'sin teléfono'}
                  {' · '}{d.recordatorios_enviados} recordatorio{d.recordatorios_enviados === 1 ? '' : 's'}
                  {d.ultimo_recordatorio_en && ` (último ${fechaCorta(d.ultimo_recordatorio_en)})`}
                  {d.promesa_fecha && (
                    <span className="text-success"> · promete pagar el {d.promesa_fecha}</span>
                  )}
                </div>
              </div>
              <div className="font-semibold tabular-nums text-[13px]">{cop(d.saldo)}</div>
              <Button size="sm" variant="ghost" className="shrink-0"
                aria-label={`${d.opt_out ? 'Reactivar' : 'Pausar'} recordatorios de ${d.nombre}`}
                title={d.opt_out ? 'Reactivar recordatorios' : 'Pausar recordatorios (opt-out)'}
                onClick={() => onOptOut(d)}>
                {d.opt_out ? <BellOff className="size-3.5" /> : <BellRing className="size-3.5" />}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function SeccionPagos({ pagos, onVerificar }) {
  return (
    <Card className="p-3">
      <h2 className="text-sm font-semibold mb-2 inline-flex items-center gap-1.5">
        <Inbox className="size-4 text-primary" /> Pagos por verificar
      </h2>
      {pagos.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Nada por verificar.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {pagos.map(p => (
            <li key={p.id} className="py-2.5 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-[13px]">{p.telefono}</div>
                <div className="text-[12px] text-muted-foreground line-clamp-2">
                  {p.nota || 'Sin detalle'} · {fechaCorta(p.creado_en)}
                </div>
              </div>
              <Button size="sm" onClick={() => onVerificar(p)}>Verificado</Button>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">
        Verificar el comprobante NO registra el abono: hazlo en Clientes/Fiados (mueve caja).
      </p>
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
      cadencia_dias: Number(f.cadencia_dias) || 7,
      max_recordatorios: Number(f.max_recordatorios) || 3,
      hora_inicio: f.hora_inicio,
      hora_fin: f.hora_fin,
      saldo_minimo: String(f.saldo_minimo ?? '0'),
    }
    await enviar('/cobranza/config', 'PUT', body, 'Configuración guardada', refetch)
  }

  const campo = (label, k, type = 'number') => (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <Input type={type} value={f[k] ?? ''} onChange={set(k)} aria-label={label} className="h-9" />
    </label>
  )

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Reglas de cobranza</h3>
      <div className="grid grid-cols-2 gap-2.5">
        {campo('Cadencia (días)', 'cadencia_dias')}
        {campo('Máx. recordatorios', 'max_recordatorios')}
        {campo('Desde (hora)', 'hora_inicio', 'time')}
        {campo('Hasta (hora)', 'hora_fin', 'time')}
        {campo('Saldo mínimo ($)', 'saldo_minimo')}
        <label className="inline-flex items-center gap-2 text-sm self-end pb-2">
          <input type="checkbox" checked={!!f.activo} aria-label="Cobranza activa"
            onChange={e => setF(p => ({ ...p, activo: e.target.checked }))} />
          Cobranza activa
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button onClick={guardar}>Guardar reglas</Button>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        El tono del agente es respetuoso siempre (fijado por el sistema). Los recordatorios solo
        salen en la ventana horaria y respetan promesas de pago y opt-out.
      </p>
    </Card>
  )
}

export default function TabCartera() {
  const { isAdmin } = useAuth()
  // El backend de /cobranza es 100% admin (403 para staff): sin rol, ni siquiera se consulta.
  if (!isAdmin()) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        La cartera es información sensible: solo un administrador puede verla.
      </Card>
    )
  }
  return <CarteraAdmin />
}

function CarteraAdmin() {
  const deudoresQ = useFetch('/cobranza/deudores')
  const pagosQ = useFetch('/cobranza/pagos-reportados')
  const promesasQ = useFetch('/cobranza/promesas?estado=vigente')
  const configQ = useFetch('/cobranza/config')
  const recuperadoQ = useFetch('/cobranza/recuperado?dias=30')

  useRealtimeEvent(EVENTOS, () => {
    deudoresQ.refetch(); pagosQ.refetch(); promesasQ.refetch(); recuperadoQ.refetch()
  })

  const deudores = arr(deudoresQ.data)
  const pagos = arr(pagosQ.data)
  const promesas = arr(promesasQ.data)
  const total = deudores.reduce((acc, d) => acc + Number(d.saldo || 0), 0)

  function onOptOut(d) {
    return enviar(
      `/cobranza/clientes/${d.cliente_id}/opt-out`, 'PUT', { opt_out: !d.opt_out },
      d.opt_out ? 'Recordatorios reactivados' : 'Recordatorios pausados', deudoresQ.refetch,
    )
  }

  function onVerificar(p) {
    return enviar(
      `/cobranza/pagos-reportados/${p.id}/verificar`, 'POST', null,
      'Pago marcado como verificado', pagosQ.refetch,
    )
  }

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <HandCoins className="size-4.5 text-primary" /> Cartera
      </h1>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Kpi label="En cartera" value={deudoresQ.loading ? '…' : cop(total)} />
        <Kpi label="Recuperado" value={recuperadoQ.loading ? '…' : cop(recuperadoQ.data?.total)}
          hint="últimos 30 días, tras recordatorio" />
        <Kpi label="Deudores" value={deudoresQ.loading ? '…' : deudores.length} />
        <Kpi label="Promesas vigentes" value={promesasQ.loading ? '…' : promesas.length} />
        <Kpi label="Pagos por verificar" value={pagosQ.loading ? '…' : pagos.length} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SeccionDeudores deudores={deudores} onOptOut={onOptOut} />
        <div className="space-y-3">
          <SeccionPagos pagos={pagos} onVerificar={onVerificar} />
          <SeccionConfig config={configQ.data} refetch={configQ.refetch} />
        </div>
      </div>

      {/* Cartera de alquiler (vertical construcción, flag `cartera_alquiler`): se auto-gatea por la
          capacidad — si la empresa no la tiene, no pinta ni pide nada. */}
      <CarteraAlquilerSection />
    </div>
  )
}
