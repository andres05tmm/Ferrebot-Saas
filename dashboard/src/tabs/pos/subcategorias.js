/*
 * Subcategorías del POS — copiadas 1:1 del FerreBot viejo (ventasRapidas.helpers.js, "misma lógica
 * que /productos en el bot"): dentro de una categoría, chips que parten el surtido por palabra clave
 * del NOMBRE del producto (Brochas/Rodillos, Lijas, Drywall ×6…). Así el cajero encuentra sin leer
 * 200 cards. Funciones puras, sin React.
 *
 * White-label: las subcategorías se activan por regex sobre el nombre de la CATEGORÍA del tenant
 * (ferretería/pinturas/tornillería); una categoría sin reglas simplemente no muestra la fila.
 * "Varios"/"Otros" es el resto calculado (lo que ninguna otra subcategoría atrapó) — mismo efecto
 * que las listas de exclusión del viejo sin duplicar keywords.
 */
import {
  Brush, Circle, Cog, Disc, Droplet, Droplets, FlaskConical, Hammer, Hexagon,
  Link, Lock, Package, Paintbrush, Palette, Pin, Ruler, SprayCan, Wand,
} from 'lucide-react'
import { normalizarLocal } from './filtroLocal.js'

const nl = (p) => normalizarLocal(p.nombre)
const sinEspacios = (p) => nl(p).replace(/ /g, '')

const SUBCATS = [
  {
    re: /ferreter/i,
    subs: [
      { key: 'ferr_brochas', Icono: Brush, label: 'Brochas / Rodillos', fn: p => nl(p).includes('brocha') || nl(p).includes('rodillo') },
      { key: 'ferr_lijas', Icono: Ruler, label: 'Lijas', fn: p => nl(p).includes('lija') || nl(p).includes('esmeril') },
      { key: 'ferr_cintas', Icono: Link, label: 'Cintas', fn: p => nl(p).includes('cinta') || nl(p).includes('pele') || nl(p).includes('enmascarar') },
      { key: 'ferr_cerraduras', Icono: Lock, label: 'Cerraduras', fn: p => ['cerradura', 'candado', 'cerrojo', 'falleba'].some(k => nl(p).includes(k)) },
      { key: 'ferr_brocas', Icono: Disc, label: 'Brocas / Discos', fn: p => nl(p).includes('broca') || nl(p).includes('disco') },
      { key: 'ferr_herr', Icono: Hammer, label: 'Herramientas', fn: p => ['martillo', 'metro', 'destornillador', 'exacto', 'espatula', 'tijera', 'formon', 'grapadora', 'machete', 'taladro', 'llave', 'pulidora'].some(k => nl(p).includes(k)) },
      { key: 'ferr_varios', Icono: Package, label: 'Varios', resto: true },
    ],
  },
  {
    re: /pintur|disolvente|solvente/i,
    subs: [
      { key: 'pint_vinilo', Icono: Paintbrush, label: 'Vinilo / Cuñetes', fn: p => nl(p).includes('vinilo') || /cu[ñn]ete/i.test(p.nombre) },
      { key: 'pint_esmalte', Icono: Palette, label: 'Esmalte / Anticorr.', fn: p => nl(p).includes('esmalte') || nl(p).includes('anticorrosivo') },
      { key: 'pint_laca', Icono: Wand, label: 'Laca', fn: p => nl(p).includes('laca') },
      { key: 'pint_thinner', Icono: FlaskConical, label: 'Thinner / Varsol', fn: p => nl(p).includes('thinner') || nl(p).includes('varsol') || nl(p).includes('tiner') },
      { key: 'pint_poli', Icono: Droplet, label: 'Poliuretano', fn: p => nl(p).includes('poliuretano') || nl(p).includes('poliamida') },
      { key: 'pint_aerosol', Icono: SprayCan, label: 'Aerosol', fn: p => nl(p).includes('aerosol') || nl(p).includes('aersosol') },
      { key: 'pint_sellador', Icono: Droplets, label: 'Sellador / Masilla', fn: p => nl(p).includes('sellador') || nl(p).includes('masilla') },
      { key: 'pint_otros', Icono: Palette, label: 'Otros', resto: true },
    ],
  },
  {
    re: /tornill|puntill|fijacion|fijación/i,
    subs: [
      { key: 'torn_dry6', Icono: Cog, label: 'Drywall ×6', fn: p => nl(p).includes('drywall') && /6x/.test(sinEspacios(p)) },
      { key: 'torn_dry8', Icono: Cog, label: 'Drywall ×8', fn: p => nl(p).includes('drywall') && /8x/.test(sinEspacios(p)) },
      { key: 'torn_dry10', Icono: Cog, label: 'Drywall ×10', fn: p => nl(p).includes('drywall') && /10x/.test(sinEspacios(p)) },
      { key: 'torn_hex', Icono: Hexagon, label: 'Hex Galvanizado', fn: p => nl(p).includes('hex') && (nl(p).includes('tornillo') || nl(p).includes('tuerca') || (nl(p).includes('arandela') && nl(p).includes('galv'))) },
      { key: 'torn_estufa', Icono: Cog, label: 'Estufa', fn: p => nl(p).includes('estufa') },
      { key: 'torn_puntillas', Icono: Pin, label: 'Puntillas', fn: p => nl(p).includes('puntilla') },
      { key: 'torn_tirafondo', Icono: Cog, label: 'Tira Fondo', fn: p => nl(p).includes('tira fondo') },
      { key: 'torn_arandelas', Icono: Circle, label: 'Arandelas / Chazos', fn: p => (nl(p).includes('arandela') || nl(p).includes('chazo')) && !nl(p).includes('galv') },
    ],
  },
]

/** Subcategorías de una categoría del tenant; [] si no hay reglas para ella. */
export function subcatsDe(categoria = '') {
  return SUBCATS.find(s => s.re.test(categoria))?.subs || []
}

/** Filtra por la subcategoría elegida. `resto` = lo que ninguna hermana con `fn` atrapó. */
export function filtrarSubcat(items, subs, key) {
  const sub = subs.find(s => s.key === key)
  if (!sub) return items
  if (sub.resto) {
    const conFn = subs.filter(s => s.fn)
    return items.filter(p => !conFn.some(s => s.fn(p)))
  }
  return items.filter(sub.fn)
}

// Orden de tornillería del viejo: tornillos drywall primero (×6 → ×8 → ×10, y dentro por largo),
// el resto por precio ascendente. En cualquier otra categoría no reordena.
function esDrywall(p) { return nl(p).includes('drywall') && nl(p).includes('tornillo') }
function drySize(p) { const m = sinEspacios(p).match(/(\d+)x/); return m ? parseInt(m[1], 10) : 99 }
function dryLen(p) { const m = sinEspacios(p).match(/x(\d+(?:\.\d+)?)/); return m ? parseFloat(m[1]) : 999 }

export function ordenarProductos(categoria, items) {
  if (!/tornill/i.test(categoria)) return items
  const dry = items.filter(esDrywall).sort((a, b) => drySize(a) - drySize(b) || dryLen(a) - dryLen(b))
  const resto = items.filter(p => !esDrywall(p))
    .sort((a, b) => Number(a.precio_venta) - Number(b.precio_venta))
  return [...dry, ...resto]
}
