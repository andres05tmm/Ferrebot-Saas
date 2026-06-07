/*
 * util.jsx — helpers del pack Agenda en el dashboard: fechas en hora Colombia (regla #4), etiquetas
 * y badge de estado de cita. El backend emite/recibe ISO con offset -05:00; aquí no se asume la zona
 * del navegador: las fechas de filtro son YYYY-MM-DD en Colombia y el datetime-local se sella a -05:00.
 */
export const TIPOS_RECURSO = ['profesional', 'sala', 'equipo', 'mesa', 'cancha']
export const DIAS_SEMANA = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
export const ESTADOS = ['pendiente', 'confirmada', 'cumplida', 'cancelada', 'no_show']

const ESTADO_LABEL = {
  pendiente: 'Pendiente', confirmada: 'Confirmada', cumplida: 'Cumplida',
  cancelada: 'Cancelada', no_show: 'No asistió',
}
const ESTADO_CLASE = {
  pendiente: 'bg-warning/15 text-warning',
  confirmada: 'bg-success/15 text-success',
  cumplida: 'bg-primary/10 text-primary',
  cancelada: 'bg-surface-2 text-muted-foreground',
  no_show: 'bg-destructive/15 text-destructive',
}

export function EstadoBadge({ estado }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${ESTADO_CLASE[estado] || 'bg-surface-2 text-muted-foreground'}`}>
      {ESTADO_LABEL[estado] || estado}
    </span>
  )
}

/** Hoy en Colombia como YYYY-MM-DD (sin ambigüedad de zona del navegador). */
export function hoyCO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

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
