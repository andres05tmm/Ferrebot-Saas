/*
 * Monto abreviado para las celdas del calendario.
 *
 * En una celda de ~90px no cabe "$1.030.500", y truncarlo con ellipsis deja al dueño adivinando. El
 * dashboard viejo resolvía esto con `1,03M` / `899k` / `80,0k`, y ese formato es el que la gente ya
 * sabe leer, así que se replica en vez de inventar otro.
 *
 * Las reglas salen de mirar el original:
 *   - millones con 2 decimales      → 1.030.500 = "1,03M"
 *   - miles de 3 cifras sin decimal → 899.000   = "899k"
 *   - miles de 1-2 cifras con uno   → 80.000    = "80,0k"   (si no, "80k" se lee como poco preciso)
 *   - cero o nada                   → "—"       (un "$0" en 20 celdas es ruido)
 */
export function montoCorto(valor) {
  const v = Number(valor || 0)
  if (!v) return '—'
  const coma = (n, dec) => n.toFixed(dec).replace('.', ',')
  if (v >= 1_000_000) return `${coma(v / 1_000_000, 2)}M`
  if (v >= 1_000) {
    const miles = v / 1_000
    return miles >= 100 ? `${Math.round(miles)}k` : `${coma(miles, 1)}k`
  }
  return String(Math.round(v))
}
