/*
 * fechas.js — helpers de "hoy" en hora Colombia (UTC-5) compartidos por todo el dashboard.
 * Regla #4: nunca `new Date()` crudo como "hoy". Antes había 7 copias locales de hoyCO() (calendario,
 * historial ×2, agenda, CarteraAlquiler, PanelPresupuestoReal, FichaMaquina, TabReservas); esta es la única.
 */

// Fecha de HOY (YYYY-MM-DD) en hora Colombia. Las fechas del backend también son YYYY-MM-DD, así que
// las comparaciones lexicográficas coinciden con las cronológicas.
export function hoyStrCO() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

// Año/mes de HOY en hora Colombia ({ anio, mes } con mes 1-12), para las vistas de mes.
export function anioMesCO() {
  const s = hoyStrCO()
  return { anio: Number(s.slice(0, 4)), mes: Number(s.slice(5, 7)) }
}

// YYYY-MM-DD ± n días. Mediodía Colombia como ancla para no cruzar el borde de zona.
export function sumarDiasCO(ymd, dias) {
  const d = new Date(`${ymd}T12:00:00-05:00`)
  d.setUTCDate(d.getUTCDate() + dias)
  return d.toISOString().slice(0, 10)
}

// Ventana del parte de horas manual: hoy o hasta N días atrás, nunca futuro.
// Espeja VENTANA_DIAS_PARTE de modules/maquinaria/router.py (el guard real vive en el backend).
export const VENTANA_DIAS_PARTE = 3
