/*
 * TabInicioAgente — portada del agente IA para negocios de SERVICIOS (sin POS).
 *
 * Es la home cuando el tenant NO tiene `pos` (ver resolveHomePath en lib/features). Compone, sin
 * inventar endpoints, lo que el agente ya produce:
 *   - Pendientes de asesor → conteo de GET /conversaciones/escaladas, con enlace al inbox (canal_whatsapp).
 *   - KPIs del agente       → 5-7 tarjetas de GET /agente/reporte, según los packs activos.
 *   - Próximas citas de hoy → GET /agenda/citas?desde=hoy&hasta=hoy (pack_agenda), reusando util de agenda.
 *   - Acciones rápidas      → de servicio (Agenda, Inbox, Conocimiento, Clientes), nunca "Nueva venta".
 *
 * Cada bloque se gatea por la feature de su pack (así no se piden endpoints que darían 403) y maneja su
 * propio loading/empty: un fallo no rompe el resto. Tiempo real: re-fetch ante eventos de cita/escalada.
 * Todas las horas en zona Colombia (regla #4).
 */
import { useMemo } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import {
  CalendarClock, CalendarDays, Headset, BookText, Users, ArrowRight,
  Bot, MessageCircle, Monitor, TrendingUp, Star, HandCoins, ClipboardList, FileText,
} from 'lucide-react'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { useBranding } from '@/lib/branding.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import KpiCard from '@/components/KpiCard.jsx'
import { EstadoBadge, fmtHora, hoyCO, minutosCO } from './agenda/util.jsx'

export default function TabInicioAgente() {
  const navigate = useNavigate()
  const { refreshKey } = useOutletContext() ?? {}
  const features = useFeatures()
  const branding = useBranding()
  const deps = [refreshKey]

  const tieneAgenda = features.includes('pack_agenda')
  const tieneWhatsapp = features.includes('canal_whatsapp')

  // Citas de hoy (solo con pack_agenda). Servicios para resolver el nombre legible del bloque.
  const hoy = useMemo(() => hoyCO(), [refreshKey])
  const citasQ = useFetch(tieneAgenda ? `/agenda/citas?desde=${hoy}&hasta=${hoy}` : null, deps)
  const serviciosQ = useFetch(tieneAgenda ? '/agenda/servicios' : null, deps)

  // Pendientes de asesor + reporte del agente (solo con canal_whatsapp: el reporte lo exige el backend).
  const escaladasQ = useFetch(tieneWhatsapp ? '/conversaciones/escaladas' : null, deps)
  const reporteQ = useFetch(tieneWhatsapp ? '/agente/reporte' : null, deps)

  useRealtimeEvent(
    ['cita_agendada', 'cita_estado', 'cita_reagendada', 'cita_confirmacion',
      'conversacion_escalada', 'conversacion_resuelta', 'reconnected'],
    () => { citasQ.refetch(); escaladasQ.refetch(); reporteQ.refetch() },
  )

  const escaladas = Array.isArray(escaladasQ.data) ? escaladasQ.data : []
  const kpis = useMemo(() => construirKpis(reporteQ.data), [reporteQ.data])

  return (
    <div className="space-y-3">
      <Saludo nombre={branding?.nombre_comercial} />

      {tieneWhatsapp && (
        <BannerPendientes total={escaladas.length} onAbrir={() => navigate('/conversaciones')} />
      )}

      {kpis.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {kpis.map(k => (
            <KpiCard key={k.label} headerBand tone={k.tone} icon={k.icon} label={k.label}
              value={k.value} sub={k.sub} loading={reporteQ.loading} />
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        {tieneAgenda && (
          <ProximasCitas
            citas={citasQ.data} servicios={serviciosQ.data}
            loading={citasQ.loading} onVerAgenda={() => navigate('/agenda')}
          />
        )}
        <AccionesRapidas features={features} navigate={navigate} />
      </div>
    </div>
  )
}

function Saludo({ nombre }) {
  return (
    <div>
      <h1 className="text-lg font-semibold tracking-tight text-foreground">
        {nombre ? `Hola, ${nombre}` : 'Tu agente hoy'}
      </h1>
      <p className="text-[13px] text-muted-foreground">Lo que tu agente está atendiendo en este momento.</p>
    </div>
  )
}

// ── Banner de pendientes de asesor ───────────────────────────────────────────
function BannerPendientes({ total, onAbrir }) {
  const hay = total > 0
  return (
    <Card
      role="button" tabIndex={0} onClick={onAbrir}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onAbrir() } }}
      aria-label="Abrir inbox de conversaciones"
      className={`p-3.5 flex items-center gap-3 cursor-pointer transition-all duration-base ease-out-quad hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 ${hay ? 'border-warning/30 bg-warning/[0.04]' : 'border-border'}`}
    >
      <span className={`grid place-items-center rounded-md size-9 shrink-0 ${hay ? 'bg-warning text-white' : 'bg-surface-2 text-muted-foreground'}`}>
        <Headset className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-foreground">
          {hay
            ? `${total} ${total === 1 ? 'cliente esperando' : 'clientes esperando'} asesor`
            : 'Sin clientes esperando asesor'}
        </div>
        <div className="text-[12px] text-muted-foreground">
          {hay ? 'Atiéndelos en el inbox y devuélvelos al bot al terminar.' : 'El agente está atendiendo todo.'}
        </div>
      </div>
      <span className="shrink-0 inline-flex items-center gap-1 text-[12px] font-medium text-primary">
        Abrir inbox <ArrowRight className="size-3.5" aria-hidden="true" />
      </span>
    </Card>
  )
}

// ── KPIs del agente (de GET /agente/reporte, máx. 7 según packs) ──────────────
// Construye las tarjetas en orden de prioridad y recorta a 7. Cada bloque solo aparece si su pack está
// activo (el backend ya gatea el reporte por capacidad: si el bloque no vino, no se pinta su KPI).
export function construirKpis(reporte) {
  if (!reporte) return []
  const kpis = []
  const conv = reporte.conversaciones
  if (conv) {
    if (conv.pct_resueltas_sin_humano !== null && conv.pct_resueltas_sin_humano !== undefined) {
      kpis.push({
        label: 'Resueltas sin humano', tone: 'success', icon: Bot,
        value: `${conv.pct_resueltas_sin_humano}%`, sub: `${num(conv.nuevas)} conversaciones`,
      })
    }
  }
  if (reporte.citas) {
    kpis.push({
      label: 'Citas', tone: 'info', icon: CalendarDays,
      value: num(reporte.citas.total), sub: `${num(reporte.citas.agendadas_por_agente)} por el agente`,
    })
  }
  if (reporte.cobranza) {
    kpis.push({
      label: 'Recuperado', tone: 'success', icon: HandCoins,
      value: cop(reporte.cobranza.recuperado), sub: `${num(reporte.cobranza.recordatorios)} recordatorios`,
    })
  }
  if (reporte.satisfaccion) {
    kpis.push({
      label: 'Satisfacción', tone: 'warning', icon: Star,
      value: num(reporte.satisfaccion.promedio), sub: `${num(reporte.satisfaccion.respuestas)} respuestas`,
    })
  }
  if (reporte.cotizaciones) {
    const pct = reporte.cotizaciones.conversion_pct
    kpis.push({
      label: 'Conversión cotiz.', tone: 'primary', icon: FileText,
      value: pct === null || pct === undefined ? '—' : `${pct}%`,
      sub: `${num(reporte.cotizaciones.aceptadas)}/${num(reporte.cotizaciones.emitidas)} aceptadas`,
    })
  }
  if (reporte.pedidos) {
    kpis.push({
      label: 'Vendido (pedidos)', tone: 'primary', icon: ClipboardList,
      value: cop(reporte.pedidos.vendido), sub: `${num(reporte.pedidos.entregados)} entregados`,
    })
  }
  if (conv) {
    kpis.push({
      label: 'Conversaciones', tone: 'info', icon: Headset,
      value: num(conv.nuevas), sub: `${num(conv.escaladas_a_humano)} a un humano`,
    })
  }
  return kpis.slice(0, 7)
}

// ── Próximas citas de hoy ─────────────────────────────────────────────────────
function ProximasCitas({ citas, servicios, loading, onVerAgenda }) {
  const nombreServicio = useMemo(
    () => Object.fromEntries((Array.isArray(servicios) ? servicios : []).map(s => [s.id, s.nombre])),
    [servicios],
  )
  const proximas = useMemo(() => {
    const arr = (Array.isArray(citas) ? citas : []).filter(c => c.estado !== 'cancelada' && c.estado !== 'no_show')
    return arr.sort((a, b) => minutosCO(a.inicio) - minutosCO(b.inicio)).slice(0, 6)
  }, [citas])

  return (
    <Card className="lg:col-span-2 p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <CalendarClock className="size-3.5" /> Próximas citas de hoy
        </h2>
        <button onClick={onVerAgenda} className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1">
          ver agenda <ArrowRight className="size-3" />
        </button>
      </div>

      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : proximas.length === 0 ? (
        <div className="py-10 flex flex-col items-center gap-2 text-muted-foreground">
          <CalendarClock className="size-5 opacity-60" aria-hidden="true" />
          <p className="text-sm">No hay citas para hoy.</p>
        </div>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {proximas.map(c => (
            <li key={c.id} className="py-2 flex items-center gap-3">
              <span className="text-[13px] font-semibold tabular-nums text-foreground w-12 shrink-0">{fmtHora(c.inicio)}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium truncate flex items-center gap-1.5">
                  {c.origen === 'whatsapp'
                    ? <MessageCircle className="size-3 shrink-0 text-success" aria-label="Por WhatsApp" />
                    : <Monitor className="size-3 shrink-0 text-muted-foreground" aria-label="Por dashboard" />}
                  <span className="truncate">{c.cliente_nombre}</span>
                </div>
                <div className="text-[11px] text-muted-foreground truncate">
                  {nombreServicio[c.servicio_id] || `Servicio #${c.servicio_id}`}
                </div>
              </div>
              <EstadoBadge estado={c.estado} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// ── Acciones rápidas de servicio (contextuales por feature) ───────────────────
function AccionesRapidas({ features, navigate }) {
  const acciones = [
    features.includes('pack_agenda') && { label: 'Ver agenda', icon: CalendarDays, tone: 'info', to: '/agenda' },
    features.includes('canal_whatsapp') && { label: 'Abrir inbox', icon: Headset, tone: 'primary', to: '/conversaciones' },
    features.includes('pack_faq') && { label: 'Conocimiento', icon: BookText, tone: 'success', to: '/conocimiento' },
    { label: 'Clientes', icon: Users, tone: 'warning', to: '/clientes' },
  ].filter(Boolean)

  const toneStyles = {
    primary: { color: 'hsl(var(--accent))', bg: 'bg-primary/10' },
    warning: { color: 'hsl(var(--warning))', bg: 'bg-warning/10' },
    info: { color: 'hsl(var(--info))', bg: 'bg-info/10' },
    success: { color: 'hsl(var(--success))', bg: 'bg-success/10' },
  }

  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <TrendingUp className="size-3.5" /> Acciones rápidas
        </h2>
      </div>
      <div className="grid grid-cols-1 gap-2">
        {acciones.map(a => {
          const t = toneStyles[a.tone]
          const Icon = a.icon
          return (
            <button key={a.label} onClick={() => navigate(a.to)}
              className="group flex items-center gap-2.5 p-3 rounded-md border border-border bg-surface hover:border-primary/40 hover:bg-primary/[0.03] transition-colors text-left">
              <span className={`grid place-items-center rounded-md size-8 shrink-0 ${t.bg}`} style={{ color: t.color }}>
                <Icon className="size-4" />
              </span>
              <span className="text-[13px] font-medium truncate">{a.label}</span>
              <ArrowRight className="size-3.5 ml-auto text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
            </button>
          )
        })}
      </div>
    </Card>
  )
}
