/*
 * util.js — helpers del Calendario de obra (vertical construcción). Aislado en su propio módulo para
 * que el contenedor y `DetalleDia` compartan `qsEntidad` SIN import circular (DetalleDia lo importa el
 * contenedor). Zona horaria Colombia (regla #4): nunca `new Date()` crudo para fechas de calendario.
 */

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
