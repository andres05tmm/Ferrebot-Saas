/*
 * piezas.jsx — chrome y helpers compartidos por el cockpit `/panel` (F3, vertical construcción).
 * Concentra lo que las secciones repiten para que el tablero se lea como una sola pieza:
 *
 *   - `SeccionPanel` — Card de sección con header EN VERSALITAS (Oswald / font-display), icono muted y
 *                      una acción opcional a la derecha (p. ej. "Ver todas →"). Es el patrón de card
 *                      sobria de los tabs de construcción, subido a la tipografía industrial del cockpit.
 *   - `n`            — coerción numérica robusta (dinero llega como STRING decimal del backend).
 *   - `pctConsumido` — % de presupuesto consumido (gasto / ingreso presupuestado); null si no hay
 *                      presupuesto (la UI pinta "—", no un 0% engañoso).
 *   - motion         — la entrada sutil (opacity + translate/scaleX) respeta prefers-reduced-motion vía
 *                      el <MotionConfig reducedMotion="user"> que envuelve la página.
 *
 * Estética (dirección "gremio industrial"): ámbar solo como marca/acción; el riesgo va con `Semaforo`
 * (punto + texto, nunca color-solo). Tokens semánticos → dark mode del tema 'obra' sin tocar nada aquí.
 */
import { motion } from 'framer-motion'
import { Card } from '@/components/ui/card.jsx'

const MotionCard = motion.create(Card)

/** Coerción numérica: el dinero del backend es STRING decimal (Decimal sin float). 0 ante basura. */
export const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

/** % de presupuesto consumido = gasto / ingreso presupuestado. `null` si no hay presupuesto contra el
 *  que medir (la UI muestra "—", nunca un 0% que se leería como "sin gastar"). */
export function pctConsumido(gasto, ingresoPresupuestado) {
  const ing = n(ingresoPresupuestado)
  if (ing <= 0) return null
  return Math.round((n(gasto) / ing) * 100)
}

// Entrada de sección: sube y aparece. El desplazamiento (transform) lo suprime MotionConfig cuando el
// usuario pide menos movimiento; la opacidad queda como transición mínima.
const ENTRADA = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.28, ease: [0.22, 1, 0.36, 1] } },
}

/**
 * SeccionPanel — Card de sección del cockpit con header en versalitas.
 * Props: `icon` (lucide), `titulo`, `accion` (nodo a la derecha), `contentClassName`, `className`.
 * El resto de props (aria-label, etc.) pasan al contenedor.
 */
export function SeccionPanel({ icon: Icon, titulo, accion, contentClassName = '', className = '', children, ...rest }) {
  return (
    <MotionCard
      variants={ENTRADA}
      initial="hidden"
      animate="visible"
      className={`overflow-hidden p-0 ${className}`}
      {...rest}
    >
      <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-2.5">
        {Icon && <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
        <h2 className="font-display text-[13px] font-semibold uppercase tracking-wide text-foreground">{titulo}</h2>
        {accion && <div className="ml-auto flex items-center">{accion}</div>}
      </div>
      <div className={contentClassName}>{children}</div>
    </MotionCard>
  )
}
