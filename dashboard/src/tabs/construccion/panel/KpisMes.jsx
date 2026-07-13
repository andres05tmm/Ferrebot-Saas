/*
 * KpisMes — la fila de indicadores del mes en el cockpit del dueño. TRES teselas, riesgo a la vista:
 *   1. Ingreso del mes  (alquiler + resbalos)  · Δ% vs mes anterior — subir es BUENO (verde ↑).
 *   2. Gastos del mes    (gastos + compras)     · Δ% vs mes anterior — subir es MALO (rojo ↑, color invertido).
 *   3. Utilidad estimada (numeral Oswald grande) + `Semaforo` del margen (rojo <0 · ámbar 0–3% · verde ≥3%).
 *
 * La cuarta tesela ("Flujo de caja") murió en F2.4: el backend la sirve como ALIAS de la utilidad (v1
 * documentado) y el dueño veía el mismo número dos veces con subtítulos distintos (hallazgo F1). Vuelve
 * solo cuando exista el cálculo real de flujo.
 *
 * Dinero llega como STRING decimal → se formatea con cop(). Numerales en font-display (Oswald); el ámbar
 * es marca, jamás semáforo: el riesgo del margen va con la píldora `Semaforo`.
 */
import { motion } from 'framer-motion'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo } from '../comunes.jsx'
import { n } from './piezas.jsx'

const MotionCard = motion.create(Card)

// Δ% actual vs anterior. Sin base (anterior 0) no se inventa porcentaje: 'nuevo' si hay actual, null si nada.
function calcDelta(actual, anterior) {
  const a = n(actual), b = n(anterior)
  if (b === 0) return a === 0 ? null : { dir: 'new' }
  const pct = Math.round(((a - b) / Math.abs(b)) * 100)
  return { pct, dir: pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat' }
}

// Badge de tendencia. `subirEsBueno=false` invierte el color (gastos: más gasto = peor).
function Delta({ delta, subirEsBueno = true }) {
  if (!delta) return null
  if (delta.dir === 'new') {
    return <span className="text-[11px] font-medium text-muted-foreground">nuevo</span>
  }
  const Icon = delta.dir === 'up' ? TrendingUp : delta.dir === 'down' ? TrendingDown : Minus
  const bueno = delta.dir === 'flat' ? null : (delta.dir === 'up') === subirEsBueno
  const color = bueno == null ? 'text-muted-foreground' : bueno ? 'text-success' : 'text-destructive'
  return (
    <span className={`inline-flex items-center gap-0.5 text-[11px] font-semibold tabular-nums ${color}`}>
      <Icon className="size-3" aria-hidden="true" />
      {Math.abs(delta.pct)}%
    </span>
  )
}

const TESELA = {
  hidden: { opacity: 0, y: 10 },
  visible: (i) => ({ opacity: 1, y: 0, transition: { duration: 0.3, delay: i * 0.05, ease: [0.22, 1, 0.36, 1] } }),
}

// Utilidad: la píldora de margen. amarillo del backend → 'ambar' del Semaforo.
const MARGEN = {
  rojo:     { tono: 'rojo',  label: 'En pérdida' },
  amarillo: { tono: 'ambar', label: 'Margen ajustado' },
  verde:    { tono: 'verde', label: 'Saludable' },
}

const NUM_GRANDE = 'font-display font-semibold tabular-nums'

export default function KpisMes({ kpis }) {
  if (!kpis) return null
  const ant = kpis.mes_anterior || {}
  const deltaIngreso = calcDelta(kpis.ingreso_total, ant.ingreso_total)
  const deltaGasto = calcDelta(kpis.gasto_total, ant.gasto_total)

  const utilidad = n(kpis.utilidad_estimada)
  const margen = MARGEN[kpis.semaforo_utilidad] || MARGEN.verde
  const tonoUtil = utilidad < 0 ? 'text-destructive' : 'text-foreground'

  const tiles = [
    {
      label: 'Ingreso del mes',
      numeral: <span className={`${NUM_GRANDE} text-2xl text-foreground`}>{cop(n(kpis.ingreso_total))}</span>,
      tendencia: <Delta delta={deltaIngreso} subirEsBueno />,
      sub: `Alquiler ${cop(n(kpis.ingreso_alquiler))} · resbalos ${cop(n(kpis.resbalos))}`,
    },
    {
      label: 'Gastos del mes',
      numeral: <span className={`${NUM_GRANDE} text-2xl text-foreground`}>{cop(n(kpis.gasto_total))}</span>,
      tendencia: <Delta delta={deltaGasto} subirEsBueno={false} />,
      sub: `Gastos ${cop(n(kpis.gastos))} · compras ${cop(n(kpis.compras))}`,
    },
    {
      label: 'Utilidad estimada',
      pill: <Semaforo tono={margen.tono}>{margen.label}</Semaforo>,
      numeral: <span className={`${NUM_GRANDE} text-3xl ${tonoUtil}`}>{cop(utilidad)}</span>,
      sub: `Margen ${n(kpis.margen_pct).toLocaleString('es-CO', { maximumFractionDigits: 1 })}% del ingreso`,
    },
  ]

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      {tiles.map((t, i) => (
        <MotionCard key={t.label} custom={i} variants={TESELA} initial="hidden" animate="visible" className="flex flex-col gap-1.5 p-4">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{t.label}</span>
            {t.pill && <span className="ml-auto">{t.pill}</span>}
          </div>
          <div className="flex items-baseline gap-2">
            {t.numeral}
            {t.tendencia}
          </div>
          <div className="text-[11px] text-muted-foreground">{t.sub}</div>
        </MotionCard>
      ))}
    </div>
  )
}
