/*
 * Favoritos del POS: ids de producto marcados con estrella por el cajero, por navegador
 * (localStorage, como el FerreBot viejo). Sin backend: es una preferencia del puesto de trabajo.
 */
import { guardarLS, leerLS } from './piezas.jsx'

export const FAV_KEY = 'pos_favs_v1'

export function leerFavs() {
  return new Set(leerLS(FAV_KEY, []))
}

export function toggleFav(favs, productoId) {
  const nuevo = new Set(favs)
  if (nuevo.has(productoId)) nuevo.delete(productoId)
  else nuevo.add(productoId)
  guardarLS(FAV_KEY, [...nuevo])
  return nuevo
}
