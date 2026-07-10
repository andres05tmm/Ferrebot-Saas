/*
 * util.js — helpers del Calendario de obra (vertical construcción). Aislado en su propio módulo para
 * que el contenedor y `DetalleDia` compartan `qsEntidad` SIN import circular (DetalleDia lo importa el
 * contenedor). Zona horaria Colombia (regla #4): nunca `new Date()` crudo para fechas de calendario.
 */

// Eventos SSE que mueven la actividad de la obra: repintan los dots del mes y refrescan el estado actual
// (máquinas/trabajadores en obra). Compartido por CalendarioObra y EstadoActual para suscribir lo mismo.
export const EVENTOS_CALENDARIO = [
  'reconnected', 'registro_horas_creado', 'mantenimiento_registrado', 'asistencia_registrada',
  'asignacion_maquina_actualizada', 'asignacion_trabajador_actualizada', 'obra_actualizada',
]

export const DIAS_SEMANA = ['L', 'M', 'X', 'J', 'V', 'S', 'D']
export const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
  'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

// Año/mes de HOY en hora Colombia (UTC-5). Espeja `hoyCO` de historial/VistaMes.jsx.
export function hoyCO() {
  const s = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
  return { anio: Number(s.slice(0, 4)), mes: Number(s.slice(5, 7)) }
}

// Fecha de HOY (YYYY-MM-DD) en hora Colombia, para marcar el día actual en la grilla.
export function hoyStrCO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

// Querystring de los filtros de ENTIDAD (no la vista): solo agrega los ids con valor. Comparte el
// contrato del backend (obra_id/maquina_id/trabajador_id). Devuelve '' o '&clave=valor…' (concatenable).
export function qsEntidad({ obraId, maquinaId, trabajadorId } = {}) {
  const p = []
  if (obraId) p.push(`obra_id=${obraId}`)
  if (maquinaId) p.push(`maquina_id=${maquinaId}`)
  if (trabajadorId) p.push(`trabajador_id=${trabajadorId}`)
  return p.length ? `&${p.join('&')}` : ''
}

// YYYY-MM-DD → fecha legible es-CO (medio día para no cruzar el borde de zona). Ej. "miércoles, 9 de julio de 2026".
export function fechaLarga(ymd) {
  if (!ymd) return ''
  const s = new Date(`${ymd}T12:00:00-05:00`).toLocaleDateString('es-CO',
    { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'America/Bogota' })
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// Horas abreviadas para la celda/cabecera (12.5 → "12.5h", 8 → "8h"). '' si no hay horas.
export function abreviarHoras(total) {
  const n = Number(total)
  if (!n) return ''
  return `${n % 1 === 0 ? n : Number(n.toFixed(1))}h`
}

// Horas en formato humano para el detalle/estado: "6 h", "6.5 h", "0 h". Number() ya colapsa los ceros
// colgantes del decimal string del backend ("6.0000" → 6, "6.5000" → 6.5). Sin valor válido → "0 h".
export function h(x) {
  const n = Number(x)
  return `${Number.isFinite(n) ? n : 0} h`
}

// YYYY-MM-DD → fecha corta legible es-CO ("9 may 2026"). Mediodía Colombia para no cruzar el borde de
// zona. Se arma por partes (formatToParts) para no arrastrar los "de" ni el punto que mete el locale.
export function fechaCorta(ymd) {
  if (!ymd) return ''
  const d = new Date(`${ymd}T12:00:00-05:00`)
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'America/Bogota' })
      .formatToParts(d).filter((x) => x.type !== 'literal').map((x) => [x.type, x.value]),
  )
  return `${p.day} ${p.month.replace('.', '')} ${p.year}`
}

// "08:00" → "8:00" (quita el cero inicial de la hora, conserva los minutos). '' si no viene. La rotación
// de operadores manda las franjas como "HH:MM"; aquí se humanizan para la sublínea del turno.
export function horaCorta(hhmm) {
  if (!hhmm) return ''
  const [hh, mm = '00'] = String(hhmm).split(':')
  return `${Number(hh)}:${mm}`
}

// Franja de un turno como "8:00–13:00" (en-dash sin espacios). Si falta un extremo, muestra el que haya;
// si no hay ninguno, ''. Sirve para "Juan · 8:00–13:00 · 5 h" del desglose de rotación.
export function franjaTurno(inicio, fin) {
  const a = horaCorta(inicio)
  const b = horaCorta(fin)
  if (a && b) return `${a}–${b}`
  return a || b || ''
}

// YYYY-MM-DD → día y mes SIN año ("9 may"). Para la franja de estado actual, donde el año se sobreentiende.
export function fechaDiaMes(ymd) {
  if (!ymd) return ''
  const d = new Date(`${ymd}T12:00:00-05:00`)
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('es-CO', { day: 'numeric', month: 'short', timeZone: 'America/Bogota' })
      .formatToParts(d).filter((x) => x.type !== 'literal').map((x) => [x.type, x.value]),
  )
  return `${p.day} ${p.month.replace('.', '')}`
}
