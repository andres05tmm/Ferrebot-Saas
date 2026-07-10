/*
 * Iconografía por categoría del POS (réplica del FerreBot viejo, white-label).
 * El viejo hardcodeaba emojis por categoría de Punto Rojo; aquí el icono se DERIVA por palabra
 * clave del nombre real de la categoría del tenant (lucide, no emoji) con fallback Package — así
 * Punto Rojo ve su vitrina icónica y cualquier otro tenant obtiene iconos razonables gratis.
 */
import {
  Blocks, Cog, Package, Palette, Wrench, Zap,
} from 'lucide-react'

const MAPA = [
  { re: /ferreter/i, icono: Wrench, color: 'text-slate-500' },
  { re: /pintur|disolvente|solvente/i, icono: Palette, color: 'text-rose-500' },
  { re: /tornill|puntill|fijacion|fijación/i, icono: Cog, color: 'text-zinc-500' },
  { re: /impermeabil|construc|cemento|yeso/i, icono: Blocks, color: 'text-orange-600' },
  { re: /electric|eléctric/i, icono: Zap, color: 'text-amber-500' },
]

export function iconoCategoria(nombre = '') {
  const hit = MAPA.find(m => m.re.test(nombre))
  return hit ? { Icono: hit.icono, color: hit.color } : { Icono: Package, color: 'text-muted-foreground' }
}

/** Nombre limpio para mostrar: sin el número de orden con que el catálogo prefija ("2 Pinturas…"). */
export function etiquetaCategoria(nombre = '') {
  return String(nombre).replace(/^\d+\s*/, '')
}
