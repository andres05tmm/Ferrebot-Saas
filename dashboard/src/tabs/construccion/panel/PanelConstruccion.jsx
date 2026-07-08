/*
 * PanelConstruccion — el cockpit del dueño de una constructora (F3, vertical PIM). Portada de la familia
 * construcción para el ADMIN: de un vistazo cada mañana, ¿alguna obra está perdiendo plata?, ¿mis
 * máquinas están produciendo?, ¿qué debo atender? Riesgo-primero: KPIs del mes → alertas → obras por
 * riesgo → máquinas → barras → actividad en vivo.
 *
 * Un SOLO request agregado y cacheado (GET /obras/dashboard, admin-only, TTL 5 min server-side) alimenta
 * todo el tablero; `useRealtimeEvent` lo refresca ante los eventos que mueven caja/obra/máquina. RBAC: el
 * vendedor no ve cifras financieras → si no es admin, se le manda a /obras (la vista operativa). El dinero
 * llega como STRING decimal y se formatea con cop(); las secciones son presentación pura.
 */
import { MotionConfig } from 'framer-motion'
import { Navigate, useOutletContext, Link } from 'react-router-dom'
import { HardHat, Plus } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth.js'
import { useFetch, ErrorMsg } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { EstadoVacio, BTN_PRIMARY } from '../comunes.jsx'
import FeedActividad from '@/components/FeedActividad.jsx'
import { n } from './piezas.jsx'
import KpisMes from './KpisMes.jsx'
import TablaObrasRiesgo from './TablaObrasRiesgo.jsx'
import BarrasUtilidad from './BarrasUtilidad.jsx'
import EstadoMaquinas from './EstadoMaquinas.jsx'
import TopMaquinasMes from './TopMaquinasMes.jsx'
import ListaAlertas from './ListaAlertas.jsx'

// Eventos que cambian el tablero (caja, obra, máquina, facturación). El endpoint responde cacheado, así
// que refetchear ante ellos es barato; 'reconnected' cubre la vuelta de un corte de red.
const EVENTOS = [
  'reconnected', 'gasto_registrado', 'gasto_aprobado', 'venta_registrada', 'venta_anulada',
  'fiado_registrado', 'fiado_abonado', 'factura_emitida', 'compra_registrada',
  'maquina_actualizada', 'obra_actualizada', 'registro_horas_creado', 'mantenimiento_registrado',
]

// Nombre del mes en hora Colombia a partir del YYYY-MM-DD del rango (medio día para no cruzar el borde de zona).
function mesLargo(ymd) {
  if (!ymd) return ''
  const d = new Date(`${ymd}T12:00:00-05:00`)
  const s = d.toLocaleDateString('es-CO', { month: 'long', year: 'numeric', timeZone: 'America/Bogota' })
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function horaCO(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' })
}

export default function PanelConstruccion() {
  const { isAdmin } = useAuth()
  const soloAdmin = isAdmin()

  const { refreshKey } = useOutletContext() ?? {}
  // El fetch se desactiva (path null) si no es admin: no pedimos un endpoint que responde 403.
  const q = useFetch(soloAdmin ? '/obras/dashboard' : null, [refreshKey])
  useRealtimeEvent(EVENTOS, q.refetch)

  // RBAC: el cockpit expone cifras financieras del mes → el vendedor va a la vista operativa.
  if (!soloAdmin) return <Navigate to="/obras" replace />

  if (q.loading && !q.data) return <PanelSkeleton />
  if (q.error) {
    return (
      <div className="space-y-4">
        <Cabecera />
        <ErrorMsg msg="No pudimos cargar el panel. Reintenta en un momento." />
      </div>
    )
  }

  const d = q.data
  const totalObras = n(d?.portafolio?.total_obras)

  return (
    <MotionConfig reducedMotion="user">
      <div className="space-y-4">
        <Cabecera mes={d?.mes} generado={d?.generado_en} />

        {totalObras === 0 ? (
          <Card className="p-2">
            <EstadoVacio
              icono={HardHat}
              titulo="El panel cobra vida con tu primera obra"
              descripcion="Cuando registres una obra verás aquí sus KPIs del mes, el semáforo de rentabilidad, el estado de la maquinaria y las alertas a atender."
            >
              <Link to="/obras" className={`${BTN_PRIMARY} h-9`}>
                <Plus className="size-4" aria-hidden="true" /> Ir a Obras
              </Link>
            </EstadoVacio>
          </Card>
        ) : (
          <>
            <KpisMes kpis={d.kpis_mes} />

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="space-y-4 lg:col-span-2">
                <ListaAlertas alertas={d.alertas} conteos={d.conteos} />
                <TablaObrasRiesgo obras={d.portafolio?.obras} />
                <EstadoMaquinas maquinas={d.maquinas} />
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <BarrasUtilidad obras={d.portafolio?.obras} />
                  <TopMaquinasMes maquinas={d.maquinas?.top_mes} />
                </div>
              </div>
              <aside className="space-y-4">
                <FeedActividad />
              </aside>
            </div>
          </>
        )}
      </div>
    </MotionConfig>
  )
}

function Cabecera({ mes, generado }) {
  const partes = [mesLargo(mes?.desde), generado && `actualizado ${horaCO(generado)}`].filter(Boolean)
  return (
    <header>
      <h1 className="font-display text-2xl font-semibold uppercase tracking-wide text-foreground">Panel</h1>
      <p className="mt-0.5 text-[13px] text-muted-foreground">
        {['Construcción', ...partes].join(' · ')}
      </p>
    </header>
  )
}

// Esqueleto de página: cabecera + teselas KPI + bloques, en vez de un spinner suelto (progressive loading).
function PanelSkeleton() {
  return (
    <div className="space-y-4" aria-hidden="true">
      <Cabecera />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i} className="h-24 animate-pulse bg-surface-2" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card className="h-40 animate-pulse bg-surface-2" />
          <Card className="h-64 animate-pulse bg-surface-2" />
        </div>
        <Card className="h-64 animate-pulse bg-surface-2" />
      </div>
    </div>
  )
}
