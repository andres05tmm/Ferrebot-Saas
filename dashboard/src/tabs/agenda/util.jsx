/*
 * util.jsx — helpers del pack Agenda en el dashboard: fechas en hora Colombia (regla #4), etiquetas
 * y badge de estado de cita. El backend emite/recibe ISO con offset -05:00; aquí no se asume la zona
 * del navegador: las fechas de filtro son YYYY-MM-DD en Colombia y el datetime-local se sella a -05:00.
 */
import { hoyStrCO } from '@/lib/fechas'

export const TIPOS_RECURSO = ['profesional', 'sala', 'equipo', 'mesa', 'cancha']
export const DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
export const ESTADOS = ['pendiente', 'confirmada', 'cumplida', 'cancelada', 'no_show']

const ESTADO_LABEL = {
  pendiente: 'Pendiente', confirmada: 'Confirmada', cumplida: 'Cumplida',
  cancelada: 'Cancelada', no_show: 'No asistió',
}
// Convención de color de estado (DESIGN.md): Pendiente=ámbar, Confirmada=esmeralda, Cumplida=azul,
// Cancelada=gris, No-show=rojo — mapeada a los tokens existentes (warning/success/info/destructive).
const ESTADO_CLASE = {
  pendiente: 'bg-warning/15 text-warning',
  confirmada: 'bg-success/15 text-success',
  cumplida: 'bg-info/15 text-info',
  cancelada: 'bg-surface-2 text-muted-foreground',
  no_show: 'bg-destructive/15 text-destructive',
}

// Acento del bloque en el calendario: borde izquierdo + fondo tenue del color de estado.
export const ESTADO_ACCENT = {
  pendiente: 'border-warning bg-warning/10',
  confirmada: 'border-success bg-success/10',
  cumplida: 'border-info bg-info/10',
  cancelada: 'border-border bg-surface-2 text-muted-foreground',
  no_show: 'border-destructive bg-destructive/10',
}

/** Una cita "requiere atención" (Revisar) si está pendiente de aprobación del negocio. */
export const requiereAtencion = (cita) => cita?.estado === 'pendiente'

export function EstadoBadge({ estado }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${ESTADO_CLASE[estado] || 'bg-surface-2 text-muted-foreground'}`}>
      {ESTADO_LABEL[estado] || estado}
    </span>
  )
}

// Sub-estado de reconfirmación (anti-no-show), paralelo a `estado`: Esperando (neutro), Reconfirmada
// (verde) y En riesgo (rojo) — espeja el color del evento en Google Calendar.
const CONFIRMACION_LABEL = { esperando: 'Esperando', reconfirmada: 'Reconfirmada', en_riesgo: 'En riesgo' }
const CONFIRMACION_CLASE = {
  esperando: 'bg-surface-2 text-muted-foreground',
  reconfirmada: 'bg-success/15 text-success',
  en_riesgo: 'bg-destructive/15 text-destructive',
}

export function ConfirmacionBadge({ confirmacion }) {
  if (!confirmacion) return null
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${CONFIRMACION_CLASE[confirmacion] || 'bg-surface-2 text-muted-foreground'}`}>
      {CONFIRMACION_LABEL[confirmacion] || confirmacion}
    </span>
  )
}

/** Hoy en Colombia como YYYY-MM-DD (alias del helper compartido para los consumidores de agenda). */
export const hoyCO = hoyStrCO

/** Hoy + `dias` en Colombia como YYYY-MM-DD. */
export function masDiasCO(dias) {
  const base = new Date(`${hoyCO()}T12:00:00-05:00`)
  base.setDate(base.getDate() + dias)
  return base.toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

/** datetime-local ('YYYY-MM-DDTHH:MM') → ISO sellado a hora Colombia (-05:00). null si vacío. */
export function aISOColombia(local) {
  if (!local) return null
  const conSegundos = local.length === 16 ? `${local}:00` : local
  return `${conSegundos}-05:00`
}

/** ISO con offset → 'vie 12/06 14:00' legible (hora Colombia). */
export function fmtFechaCO(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CO', {
    timeZone: 'America/Bogota', weekday: 'short', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** Suma `n` días a una fecha YYYY-MM-DD (en Colombia) → YYYY-MM-DD. */
export function sumarDias(ymd, n) {
  const d = new Date(`${ymd}T12:00:00-05:00`)
  d.setDate(d.getDate() + n)
  return d.toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

/** Etiqueta del día para la barra: 'Hoy · mié, 24 jun' o 'mié, 24 jun'. */
export function fmtDiaLabel(ymd) {
  const s = new Date(`${ymd}T12:00:00-05:00`).toLocaleDateString('es-CO', {
    timeZone: 'America/Bogota', weekday: 'short', day: 'numeric', month: 'short',
  })
  return ymd === hoyCO() ? `Hoy · ${s}` : s
}

/** El backend devuelve los instantes en UTC; aquí se leen SIEMPRE en hora Colombia. */
export function minutosCO(iso) {
  const hhmm = new Date(iso).toLocaleTimeString('en-GB', {
    timeZone: 'America/Bogota', hour: '2-digit', minute: '2-digit', hour12: false,
  })
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

/** 'HH:MM' en hora Colombia. */
export function fmtHora(iso) {
  return new Date(iso).toLocaleTimeString('en-GB', {
    timeZone: 'America/Bogota', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** Fecha YYYY-MM-DD de un instante en hora Colombia (para saber a qué día pertenece la cita). */
export function diaCO(iso) {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}
