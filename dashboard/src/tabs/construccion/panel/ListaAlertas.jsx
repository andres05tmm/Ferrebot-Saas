/*
 * ListaAlertas — la bandeja accionable del cockpit: lo que el dueño debe ATENDER hoy, rojas arriba. Une
 * las alertas del payload (mantenimiento vencido/próximo, obra perdida/ajustada — ya ordenadas rojo→
 * amarillo por el backend) con las derivadas de los `conteos` (gastos por revisar, colitas, cotizaciones
 * por vencer), que solo aparecen si su conteo es > 0. Cada alerta es una fila clickeable que navega a su
 * ruta (bandeja de gastos, cartera, cotizaciones, maquinaria). Sin alertas → un verde tranquilizador.
 */
import { Link } from 'react-router-dom'
import { BellRing, ChevronRight, ShieldCheck } from 'lucide-react'
import { Semaforo } from '../comunes.jsx'
import { SeccionPanel, n } from './piezas.jsx'

const SEVERIDAD = {
  rojo:     { tono: 'rojo',  label: 'Urgente' },
  amarillo: { tono: 'ambar', label: 'Atención' },
}

const plural = (cnt, sing, plu) => `${cnt} ${cnt === 1 ? sing : plu}`

// Alertas derivadas de los conteos accionables (solo si > 0). Severidad amarilla: son pendientes, no
// pérdidas. Cada una enlaza a la bandeja donde se resuelve.
function alertasDeConteos(conteos = {}) {
  const out = []
  const revisar = n(conteos.gastos_por_revisar)
  const colitas = n(conteos.colitas)
  const cotis = n(conteos.cotizaciones_por_vencer)
  if (revisar > 0) out.push({
    tipo: 'gastos_por_revisar', severidad: 'amarillo', titulo: 'Gastos por revisar',
    detalle: `${plural(revisar, 'recibo del bot', 'recibos del bot')} esperan tu visto bueno`, ruta: '/gastos',
  })
  if (colitas > 0) out.push({
    tipo: 'colitas', severidad: 'amarillo', titulo: 'Colitas de cartera',
    detalle: `${plural(colitas, 'obra', 'obras')} con saldo de alquiler estancado`, ruta: '/cartera',
  })
  if (cotis > 0) out.push({
    tipo: 'cotizaciones_por_vencer', severidad: 'amarillo', titulo: 'Cotizaciones por vencer',
    detalle: `${plural(cotis, 'cotización', 'cotizaciones')} a punto de vencer`, ruta: '/cotizaciones-obra',
  })
  return out
}

export default function ListaAlertas({ alertas = [], conteos = {} }) {
  const derivadas = alertasDeConteos(conteos)
  // El backend ya ordena `alertas` (rojo→amarillo); las derivadas (amarillas) van después.
  const todas = [...(Array.isArray(alertas) ? alertas : []), ...derivadas]

  return (
    <SeccionPanel
      icon={BellRing}
      titulo="Alertas"
      accion={todas.length > 0 ? <span className="text-[11px] tabular-nums text-muted-foreground">{todas.length}</span> : null}
      aria-label="Alertas accionables"
    >
      {todas.length === 0 ? (
        <div className="flex items-center gap-2.5 px-4 py-4 text-[13px] text-success">
          <ShieldCheck className="size-4 shrink-0" aria-hidden="true" />
          <span className="font-medium">Sin alertas — todo en orden.</span>
        </div>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {todas.map((a, i) => <FilaAlerta key={`${a.tipo}-${a.ref_id ?? i}`} alerta={a} />)}
        </ul>
      )}
    </SeccionPanel>
  )
}

function FilaAlerta({ alerta }) {
  const sev = SEVERIDAD[alerta.severidad] || SEVERIDAD.amarillo
  return (
    <li>
      <Link
        to={alerta.ruta || '/panel'}
        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
      >
        <Semaforo tono={sev.tono} className="mt-0.5 shrink-0 self-start">{sev.label}</Semaforo>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium text-foreground">{alerta.titulo}</div>
          {alerta.detalle && <div className="truncate text-[12px] text-muted-foreground">{alerta.detalle}</div>}
        </div>
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      </Link>
    </li>
  )
}
