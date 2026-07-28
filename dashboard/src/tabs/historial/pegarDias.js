/*
 * Parser de la lista de días viejos que el dueño pega.
 *
 * El material real no es un CSV limpio: es lo que sale de copiar una tabla del dashboard viejo, o de
 * pasar a máquina un cuaderno. Viene con `$`, con puntos de miles, con tabulaciones o con varios
 * espacios, y con la fecha en `07/27` o `27/07/2026` o `2026-07-27`. Rechazar todo eso obligaría a
 * limpiarlo a mano antes de pegarlo, que es justo lo que hace que nadie lo cargue nunca.
 *
 * Lógica pura y sin dependencias: se prueba sola.
 */

// Los montos colombianos usan el punto como separador de miles: "1.030.500" son un millón, no 1,03.
// Solo se trata como decimal una coma seguida de exactamente dos dígitos al final ("1.030,50").
export function parsearMonto(texto) {
  let t = String(texto).replace(/\$/g, '').replace(/\s/g, '').trim()
  if (!t) return null
  const decimales = /,(\d{2})$/.exec(t)
  if (decimales) t = t.slice(0, -3).replace(/[.,]/g, '') + '.' + decimales[1]
  else t = t.replace(/[.,]/g, '')
  if (!/^\d+(\.\d+)?$/.test(t)) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

// `anioPorDefecto` existe porque la tabla del dashboard viejo imprime "07/27" sin año: pedirle al
// dueño que lo agregue a mano en 30 filas sería absurdo.
export function parsearFecha(texto, anioPorDefecto) {
  const t = String(texto).trim()
  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t)
  if (m) return `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  m = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(t)          // 27/07/2026 (día primero)
  if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`
  m = /^(\d{1,2})[/-](\d{1,2})$/.exec(t)                     // 07/27 → mes/día, como el viejo
  if (m) return `${anioPorDefecto}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`
  return null
}

/**
 * Convierte el texto pegado en `{ dias, errores }`.
 *
 * Los renglones que no se entienden NO se descartan en silencio: vuelven en `errores` con su número
 * de línea, para que el dueño vea qué quedó afuera antes de guardar. Descartarlos callado sería
 * perder plata del reporte sin que nadie se entere.
 */
export function parsearDiasPegados(texto, anioPorDefecto) {
  const dias = []
  const errores = []
  const vistas = new Set()

  String(texto).split(/\r?\n/).forEach((linea, i) => {
    const cruda = linea.trim()
    if (!cruda) return
    // Separador: tabulación, punto y coma, o dos o más espacios. Un solo espacio NO separa, porque
    // "27/07/2026 1.030.500" y "27 de julio" se verían igual.
    const partes = cruda.split(/\t|;|\s{2,}|\s+(?=\$?[\d.,]+$)/).map(p => p.trim()).filter(Boolean)
    if (partes.length < 2) { errores.push({ linea: i + 1, texto: cruda, motivo: 'no tiene fecha y total' }); return }
    const fecha = parsearFecha(partes[0], anioPorDefecto)
    const total = parsearMonto(partes[partes.length - 1])
    if (!fecha) { errores.push({ linea: i + 1, texto: cruda, motivo: 'no se entiende la fecha' }); return }
    if (total === null) { errores.push({ linea: i + 1, texto: cruda, motivo: 'no se entiende el total' }); return }
    if (vistas.has(fecha)) { errores.push({ linea: i + 1, texto: cruda, motivo: 'fecha repetida' }); return }
    vistas.add(fecha)
    dias.push({ fecha, total })
  })

  return { dias, errores }
}
