/*
 * TabPostventa — satisfacción postventa (plan §2.6). Gateada por 'pack_postventa'.
 * TODO el backend es admin (config + respuestas con teléfono son del dueño), así que la pestaña se
 * gatea por rol aquí también. Muestra: KPI de satisfacción (promedio 1-5 y nº de respuestas), la lista
 * de respuestas (calificación + comentario) y la config del seguimiento (activo, horas, canales, umbral
 * de reseña, URL de Google Maps).
 */
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Star, MessageSquareHeart } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

function Estrellas({ n }) {
  const v = Math.round(Number(n) || 0)
  return (
    <span className="inline-flex" aria-label={`${v} de 5`}>
      {[1, 2, 3, 4, 5].map(i => (
        <Star key={i} className={`size-3.5 ${i <= v ? 'text-warning fill-warning' : 'text-border'}`} />
      ))}
    </span>
  )
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

function SeccionConfig({ config, refetch }) {
  const [f, setF] = useState(null)
  useEffect(() => { if (config && !f) setF(config) }, [config]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!f) return null
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    const body = {
      activo: !!f.activo,
      horas_tras_evento: Number(f.horas_tras_evento) || 3,
      seguir_citas: !!f.seguir_citas,
      seguir_pedidos: !!f.seguir_pedidos,
      google_maps_url: (f.google_maps_url || '').trim() || null,
      calificacion_minima_resena: Number(f.calificacion_minima_resena) || 4,
    }
    try {
      const res = await api('/postventa/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      if (res.ok) { toast.success('Configuración guardada'); refetch() }
      else if (res.status === 403) toast.error('Necesitas permisos de administrador')
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3">Seguimiento postventa</h3>
      <div className="grid grid-cols-2 gap-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Horas tras el evento</span>
          <Input type="number" value={f.horas_tras_evento ?? ''} onChange={set('horas_tras_evento')}
            aria-label="Horas tras el evento" className="h-9" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Calificación mín. reseña</span>
          <Input type="number" min="1" max="5" value={f.calificacion_minima_resena ?? ''} onChange={set('calificacion_minima_resena')}
            aria-label="Calificación mínima para reseña" className="h-9" />
        </label>
      </div>
      <label className="flex flex-col gap-1 mt-2.5">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground">URL de Google Maps</span>
        <Input value={f.google_maps_url ?? ''} onChange={set('google_maps_url')}
          placeholder="https://maps.google.com/…" aria-label="URL de Google Maps" className="h-9" />
      </label>
      <div className="flex flex-wrap gap-4 mt-3">
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.activo} aria-label="Seguimiento activo"
            onChange={e => setF(p => ({ ...p, activo: e.target.checked }))} />
          Activo
        </label>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.seguir_citas} aria-label="Tras citas"
            onChange={e => setF(p => ({ ...p, seguir_citas: e.target.checked }))} />
          Tras citas
        </label>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.seguir_pedidos} aria-label="Tras pedidos"
            onChange={e => setF(p => ({ ...p, seguir_pedidos: e.target.checked }))} />
          Tras pedidos
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button onClick={guardar}>Guardar</Button>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Tras cada cita/pedido el agente pregunta la satisfacción (1-5). Si la calificación llega al
        umbral, invita a dejar una reseña en Google Maps; si es baja, la registra como feedback interno.
      </p>
    </Card>
  )
}

export default function TabPostventa() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-6 text-center text-sm text-muted-foreground">
        La postventa es información sensible: solo un administrador puede verla.
      </Card>
    )
  }
  return <PostventaAdmin />
}

function PostventaAdmin() {
  const satQ = useFetch('/postventa/satisfaccion')
  const respuestasQ = useFetch('/postventa/respuestas')
  const configQ = useFetch('/postventa/config')

  const sat = satQ.data || {}
  const promedio = Number(sat.promedio ?? 0)
  const total = Number(sat.respuestas ?? 0)
  const respuestas = arr(respuestasQ.data)

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <MessageSquareHeart className="size-4.5 text-primary" /> Postventa
      </h1>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Card className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Satisfacción</div>
          <div className="text-lg font-semibold tabular-nums inline-flex items-center gap-2">
            {satQ.loading ? '…' : (total > 0 ? promedio.toFixed(1) : '—')}
            {total > 0 && <Estrellas n={promedio} />}
          </div>
          <div className="text-[11px] text-muted-foreground">promedio 1-5</div>
        </Card>
        <Kpi label="Respuestas" value={satQ.loading ? '…' : total} />
        <Kpi label="Comentarios" value={respuestasQ.loading ? '…' : respuestas.filter(r => r.comentario).length} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Card className="p-3">
          <h2 className="text-sm font-semibold mb-2 inline-flex items-center gap-1.5">
            <Star className="size-4 text-warning" /> Respuestas
          </h2>
          {respuestasQ.loading ? (
            <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
          ) : respuestas.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Aún no hay respuestas de postventa.
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {respuestas.map(r => (
                <li key={r.id} className="py-2.5 flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Estrellas n={r.calificacion} />
                      <span className="text-[11px] text-muted-foreground">{fechaCorta(r.creado_en)}</span>
                    </div>
                    <div className="text-[12px] text-muted-foreground mt-0.5">
                      {r.telefono} · {r.comentario || 'sin comentario'}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <SeccionConfig config={configQ.data} refetch={configQ.refetch} />
      </div>
    </div>
  )
}
