/*
 * Filtro local del catálogo del POS (reforma grilla híbrida): funciones PURAS, sin React ni red.
 *
 * `normalizarLocal` es el espejo JS de `normalizar()` de modules/inventario/busqueda.py (lower +
 * sin tildes + ñ→n + colapsar espacios): así el filtro instantáneo del navegador y la búsqueda del
 * servidor tratan el texto igual. NFKD ya descompone la ñ; el replace explícito es cinturón y
 * tirantes, como en el Python.
 *
 * `filtrarYRankear` ordena por relevancia en 4 niveles (menor = mejor):
 *   0 código exacto · 1 nombre empieza por el término · 2 toda palabra del término prefija alguna
 *   palabra del nombre · 3 aparece en nombre/código/categoría. Empates: alfabético por nombre.
 */

export function normalizarLocal(s) {
  return String(s || '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')   // diacríticos combinantes que dejó NFKD (tildes, diéresis, virgulilla)
    .replace(/ñ/g, 'n')
    .replace(/\s+/g, ' ')
    .trim()
}

function rango(p, term, palabras) {
  const nombre = normalizarLocal(p.nombre)
  const codigo = normalizarLocal(p.codigo)
  if (codigo && codigo === term) return 0
  if (nombre.startsWith(term)) return 1
  const palabrasNombre = nombre.split(' ')
  if (palabras.every(w => palabrasNombre.some(pn => pn.startsWith(w)))) return 2
  const pajar = `${nombre} ${codigo} ${normalizarLocal(p.categoria)}`
  if (palabras.every(w => pajar.includes(w))) return 3
  return -1   // no matchea
}

export function filtrarYRankear(productos, term) {
  const t = normalizarLocal(term)
  if (!t) return []
  const palabras = t.split(' ')
  const conRango = []
  for (const p of productos) {
    const r = rango(p, t, palabras)
    if (r >= 0) conRango.push([r, p])
  }
  conRango.sort((a, b) => a[0] - b[0] || String(a[1].nombre).localeCompare(String(b[1].nombre)))
  return conRango.map(([, p]) => p)
}
