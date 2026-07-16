/*
 * cantidad.js — lógica PURA de venta por fracción / sub-unidad del POS (sin React, sin formateo).
 *
 * El motor de precios del backend (GET /productos/{id}/precio) es la fuente de verdad del total de la
 * línea; estas funciones solo (a) deciden QUÉ modal abrir según los datos del producto y (b) calculan
 * la CANTIDAD decimal a mandar + un preview del precio para el modal, a partir de los MISMOS datos que
 * usa el backend (precio_venta, fracciones, unidades_por_paquete). Así el preview del modal == el total
 * que luego pone el servidor en el carrito.
 */

// Discriminador por DATOS (no por nombre, a diferencia del dashboard viejo que casaba "esmeril").
// Devuelve el tipo de modal o null (agregar directo, cantidad 1). Los sinónimos cubren lo que pudo
// dejar el ETL desde el dashboard viejo (MLT/ML, KG/KGM); ajustar si la BD del tenant difiere.
export function tipoVenta(p) {
  const u = (p.unidad_medida || '').trim().toLowerCase()
  if (['grm', 'gramos'].includes(u)) return 'gramos'
  if (['cms'].includes(u)) return 'cm'
  if (['ml', 'mlt', 'mililitros'].includes(u)) return 'ml'
  if (['kg', 'kgm', 'kilo', 'kilos'].includes(u)) return 'kg'
  if (p.permite_fraccion && (p.fracciones?.length ?? 0) > 0) return 'fraccion'
  return null
}

// Tamaño de paquete del granel (500 g / 100 cm / 1000 ml) o null si el producto no es granel.
// Viene del backend (computed_field), que mantiene el divisor en un solo lugar.
export function paqueteDe(p) {
  return p.unidades_por_paquete != null ? Number(p.unidades_por_paquete) : null
}

// Preview del total espejando el motor del backend (obtener_precio_para_cantidad): fracción exacta →
// granel por sub-unidad → simple. No cubre el escalonado por umbral (los productos por fracción/granel
// no lo usan); da igual si difiere en un caso exótico: el TOTAL real de la línea siempre lo pone
// /precio en el carrito, esto es solo el número que se muestra en el modal.
export function previewMotor(p, cantidad) {
  const pv = Number(p.precio_venta) || 0
  const frac = fraccionQueCasa(p, cantidad)
  if (frac) return Number(frac.precio_total)
  const paquete = paqueteDe(p)
  if (paquete && paquete > 0) return (pv * cantidad) / paquete
  return pv * cantidad
}

// Fila de fracción cuyo decimal casa la cantidad (tolerancia 0.01, igual que el motor). Sirve para el
// preview y para el ½ kg "bonito".
export function fraccionQueCasa(p, cantidad) {
  return (p.fracciones || []).find(
    (f) => f.decimal != null && Math.abs(Number(f.decimal) - cantidad) < 0.01,
  ) || null
}

// Fracciones ordenadas de mayor a menor (¾, ½, ¼, ⅛…) para pintar los botones del modal de pintura.
export function fraccionesOrdenadas(p) {
  return [...(p.fracciones || [])].sort((a, b) => Number(b.decimal || 0) - Number(a.decimal || 0))
}

// Modo "pesos" del granel: cuántas sub-unidades equivalen a un monto (redondeado a 1 decimal, como el
// viejo). Ej: $2000 de puntilla ($10/g) → 200 g. Devuelve 0 si no se puede calcular.
export function subunidadesDesdePesos(p, pesos) {
  const paquete = paqueteDe(p)
  const pv = Number(p.precio_venta) || 0
  if (!paquete || pv <= 0 || !(pesos > 0)) return 0
  const precioSub = pv / paquete
  return Math.round((pesos / precioSub) * 10) / 10
}

// Precio por sub-unidad (por gramo / ml / cm) para el subtítulo del modal. null si no es granel.
export function precioSubunidad(p) {
  const paquete = paqueteDe(p)
  const pv = Number(p.precio_venta) || 0
  return paquete && paquete > 0 ? pv / paquete : null
}
