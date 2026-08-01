/*
 * Vocabulario de gastos, compartido por el tab, el modal de registro y los reportes.
 *
 * Dos ideas que el dueño necesita ver separadas (migración 0071):
 *  - CATEGORÍA: en qué se fue la plata. Las FIJAS se pagan igual vendas mucho o nada, y son las que
 *    fijan el punto de equilibrio; las variables suben y bajan con la venta.
 *  - TIPO: qué es realmente esa salida. Solo `gasto` resta en la utilidad — un retiro reparte
 *    utilidad y una inversión compra un activo que dura años.
 * Los valores canónicos son los del backend (`GastoCategoria` / `TipoEgreso` en caja/schemas.py).
 */

// [valor, etiqueta, fijo]. El orden es el del desglose: primero lo grande y fijo.
export const CATEGORIAS_GASTO = [
  ['arriendo', 'Arriendo', true],
  ['nomina', 'Nómina', true],
  ['servicios', 'Servicios (luz, agua, internet)', true],
  ['empaque', 'Empaque (bolsas, potes)', false],
  ['transporte', 'Transporte y fletes', false],
  ['mantenimiento', 'Mantenimiento', false],
  ['impuestos', 'Impuestos', false],
  ['comisiones', 'Comisiones (datáfono, 4×1000)', false],
  ['aseo', 'Aseo y cafetería', false],
  ['papeleria', 'Papelería', false],
  ['otros', 'Otros', false],
]

export const CATEGORIA_LABEL = Object.fromEntries(CATEGORIAS_GASTO.map(([v, l]) => [v, l]))
export const CATEGORIAS_FIJAS = new Set(CATEGORIAS_GASTO.filter(([, , f]) => f).map(([v]) => v))

// Los tres que se registran desde el tab. `pago_deuda` no está: pagar una factura de proveedor se
// hace en Proveedores (ahí sí descuenta la deuda), no aquí — registrarlo dos veces contaría doble.
export const TIPOS_EGRESO = [
  ['gasto', 'Gasto', 'Lo que cuesta tener la tienda abierta. Resta en la utilidad del mes.'],
  ['inversion', 'Inversión', 'Vitrina, estantería, computador: es un activo que dura años, no un gasto del mes.'],
  ['retiro', 'Retiro', 'Plata que sacas para ti. Reparte la utilidad, no la reduce.'],
]

export const TIPO_LABEL = {
  gasto: 'Gasto', inversion: 'Inversión', retiro: 'Retiro', pago_deuda: 'Pago de deuda',
}

/** Ventana [00:00, 23:59:59] hora Colombia de un rango YYYY-MM-DD, para los endpoints con datetime. */
export function rangoISO({ desde, hasta }) {
  return { desde: `${desde}T00:00:00-05:00`, hasta: `${hasta}T23:59:59-05:00` }
}

/** Hoy en Colombia como YYYY-MM-DD (nunca la fecha local del navegador). */
export const hoyCO = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })

/** Presets del selector de período, en fechas YYYY-MM-DD Colombia. */
export function periodo(id, hoyYMD) {
  const hoy = new Date(`${hoyYMD}T12:00:00-05:00`)
  const ymd = (d) => d.toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
  const menos = (dias) => { const d = new Date(hoy); d.setDate(d.getDate() - dias); return d }
  if (id === 'hoy') return { desde: hoyYMD, hasta: hoyYMD }
  if (id === 'semana') return { desde: ymd(menos(6)), hasta: hoyYMD }
  if (id === 'mes') return { desde: `${hoyYMD.slice(0, 8)}01`, hasta: hoyYMD }
  // Trimestre y año CORRIDOS (hasta hoy), no calendario cerrado: el dueño pregunta "cómo voy",
  // no "cómo cerró el trimestre pasado".
  if (id === 'trimestre') {
    const mes = Number(hoyYMD.slice(5, 7))
    const primerMes = String(mes - ((mes - 1) % 3)).padStart(2, '0')
    return { desde: `${hoyYMD.slice(0, 4)}-${primerMes}-01`, hasta: hoyYMD }
  }
  if (id === 'anio') return { desde: `${hoyYMD.slice(0, 4)}-01-01`, hasta: hoyYMD }
  // Mes pasado completo: del 1 al último día del mes anterior.
  const primeroEste = new Date(`${hoyYMD.slice(0, 8)}01T12:00:00-05:00`)
  const ultimoPasado = new Date(primeroEste); ultimoPasado.setDate(0)
  return { desde: `${ymd(ultimoPasado).slice(0, 8)}01`, hasta: ymd(ultimoPasado) }
}

export const PERIODOS = [
  ['hoy', 'Hoy'], ['semana', 'Últimos 7 días'], ['mes', 'Este mes'], ['pasado', 'Mes pasado'],
]

// Los de arriba más las ventanas largas, para Resultados financieros (una utilidad se lee por
// trimestre o por año; un gasto, no). `PERIODOS` se deja intacto para no mover los chips de Gastos.
export const PERIODOS_RESULTADOS = [
  ...PERIODOS, ['trimestre', 'Trimestre'], ['anio', 'Año corrido'],
]
