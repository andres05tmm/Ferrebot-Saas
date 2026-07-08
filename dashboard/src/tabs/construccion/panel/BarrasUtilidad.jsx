/*
 * BarrasUtilidad — top 5 obras por MAGNITUD de utilidad (|utilidad_real|), las que más mueven el
 * resultado del portafolio para bien o para mal. Barras horizontales de divs (sin librería de charts,
 * patrón del medidor de PanelPresupuestoReal): verde si la obra deja plata, rojo si la pierde. El valor
 * en $ y el margen en % van SIEMPRE en texto al lado (la barra es refuerzo, no el dato).
 *
 * La barra entra animando scaleX (transform, GPU) — no width, para no reflowear; reduced-motion la corta.
 */
import { motion } from 'framer-motion'
import { BarChart3 } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { SeccionPanel, n } from './piezas.jsx'

export default function BarrasUtilidad({ obras = [] }) {
  const top = [...obras]
    .sort((a, b) => Math.abs(n(b.utilidad_real)) - Math.abs(n(a.utilidad_real)))
    .slice(0, 5)
  const max = Math.max(1, ...top.map((o) => Math.abs(n(o.utilidad_real))))

  return (
    <SeccionPanel icon={BarChart3} titulo="Utilidad por obra" contentClassName="p-4" aria-label="Top de obras por utilidad">
      {top.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-muted-foreground">Aún no hay obras con movimiento.</p>
      ) : (
        <ul className="space-y-3">
          {top.map((o) => <BarraObra key={o.obra_id} obra={o} max={max} />)}
        </ul>
      )}
    </SeccionPanel>
  )
}

function BarraObra({ obra, max }) {
  const util = n(obra.utilidad_real)
  const positivo = util >= 0
  const ancho = Math.max(2, Math.round((Math.abs(util) / max) * 100))
  const ing = n(obra.ingreso_presupuestado)
  const margenPct = ing > 0 ? Math.round((util / ing) * 100) : null

  return (
    <li>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span className="min-w-0 flex-1 truncate text-[13px] text-secondary-foreground">{obra.nombre}</span>
        <span className={`shrink-0 text-[12px] font-semibold tabular-nums ${positivo ? 'text-success' : 'text-destructive'}`}>
          {cop(util)}{margenPct != null && <span className="ml-1 font-normal text-muted-foreground">· {margenPct}%</span>}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <motion.div
          className={`h-full rounded-full ${positivo ? 'bg-success' : 'bg-destructive'}`}
          style={{ width: `${ancho}%`, transformOrigin: 'left' }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: 1 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
    </li>
  )
}
