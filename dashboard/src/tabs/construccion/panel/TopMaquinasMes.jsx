/*
 * TopMaquinasMes — las 5 máquinas que más horas facturaron en el mes: dónde está produciendo la flota.
 * Barras horizontales de divs (ámbar = producción, la marca; no es riesgo) con horas y el ingreso del
 * mes SIEMPRE en texto al lado. Entrada con scaleX (transform, no width). Solo presentación.
 */
import { motion } from 'framer-motion'
import { Gauge } from 'lucide-react'
import { cop, num } from '@/components/shared.jsx'
import { SeccionPanel, n } from './piezas.jsx'

export default function TopMaquinasMes({ maquinas = [] }) {
  const top = [...maquinas].sort((a, b) => n(b.horas) - n(a.horas)).slice(0, 5)
  const max = Math.max(1, ...top.map((m) => n(m.horas)))

  return (
    <SeccionPanel icon={Gauge} titulo="Top máquinas del mes" contentClassName="p-4" aria-label="Máquinas más productivas del mes">
      {top.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-muted-foreground">Aún no hay horas facturadas este mes.</p>
      ) : (
        <ul className="space-y-3">
          {top.map((m) => {
            const horas = n(m.horas)
            const ancho = Math.max(2, Math.round((horas / max) * 100))
            return (
              <li key={m.maquina_id}>
                <div className="mb-1 flex items-baseline justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate text-[13px] text-secondary-foreground">{m.maquina}</span>
                  <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                    <b className="font-semibold text-foreground">{num(m.horas)} h</b> · {cop(n(m.ingreso))}
                  </span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                  <motion.div
                    className="h-full rounded-full bg-primary/80"
                    style={{ width: `${ancho}%`, transformOrigin: 'left' }}
                    initial={{ scaleX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </SeccionPanel>
  )
}
