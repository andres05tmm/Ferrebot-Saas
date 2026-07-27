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

// Preview del total espejando el motor del backend (obtener_precio_para_cantidad, 0072): empaque
// entero → fracción exacta → simple. `precio_venta` es SIEMPRE el precio de una unidad de venta (un
// kilo, un gramo), así que aquí no se divide por nada; el precio del bulto vive en `precio_paquete`.
// No cubre el escalonado por umbral (los productos por fracción/granel no lo usan); da igual si
// difiere en un caso exótico: el TOTAL real de la línea siempre lo pone /precio en el carrito, esto
// es solo el número que se muestra en el modal.
export function previewMotor(p, cantidad, { porEmpaque = false } = {}) {
  const pv = Number(p.precio_venta) || 0
  if (porEmpaque) {
    const emp = paqueteCompleto(p)
    if (emp) return (cantidad / emp.factor) * emp.precio
  }
  const frac = fraccionQueCasa(p, cantidad)
  if (frac) return Number(frac.precio_total)
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
  const pv = Number(p.precio_venta) || 0
  if (pv <= 0 || !(pesos > 0)) return 0
  return Math.round((pesos / pv) * 10) / 10
}

// Empaque que TAMBIÉN se vende entero (la bolsa de cemento de 40 kg): cuántas unidades de venta trae,
// cómo lo llama el negocio y QUÉ VALE COMPLETO. Exige las dos mitades del dato — sin `precio_paquete`
// (0072) no se puede ofrecer, porque el precio del bulto no se deduce del precio del kilo.
export function paqueteCompleto(p) {
  const factor = Number(p?.unidades_por_paquete || 0)
  const precio = p?.precio_paquete != null ? Number(p.precio_paquete) : null
  if (!factor || !p?.nombre_paquete || precio == null) return null
  return { factor, nombre: p.nombre_paquete, precio }
}

// Accesos rápidos del modo kg: las fracciones que el dueño configuró (¼, ½ …, con su precio propio)
// y después kilos enteros. Antes era una lista fija [½, 1, 1½ …] que ignoraba el dato del producto:
// el amoniaco se vende por ¼ de kilo y ese botón no existía.
export function atajosKg(p) {
  const fracs = [...(p.fracciones || [])]
    .filter((f) => f.decimal != null && Number(f.decimal) < 1)
    .sort((a, b) => Number(a.decimal) - Number(b.decimal))
    .map((f) => ({ etiqueta: `${f.fraccion} kg`, cantidad: Number(f.decimal) }))
  const enteros = [1, 2, 3].map((n) => ({ etiqueta: `${n} kg`, cantidad: n }))
  return [...fracs, ...enteros].slice(0, 6)
}
