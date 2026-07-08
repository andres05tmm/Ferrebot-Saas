/*
 * features.jsx — gating de navegación por capacidades de la empresa.
 *
 * Las features vienen de GET /config con los NOMBRES CANÓNICOS de core/tenancy/catalogo.py. El núcleo
 * (clientes, reportes) siempre llega, así que sus rutas quedan visibles; el resto (POS, fiscal, packs
 * de servicios) solo si su capacidad está activa.
 *
 * ADR 0008: el POS dejó de ser núcleo. ADR 0021: el pack se PARTIÓ en features finas — `ventas`
 * (ventas + catálogo), `caja` (caja + gastos) e `inventario` (stock + compras + proveedores) — y
 * `pos` quedó como meta-pack que las expande (el backend ya entrega el set expandido; aquí se
 * re-expande fail-safe).
 *
 * ADR 0018 (refinado por ADR 0021 §D6) — DOS FAMILIAS de dashboard. Una ruta contable es visible si
 * su feature FINA está activa y NO se trata de un tenant de atención-a-cliente cuyo retail llegó por
 * arrastre del meta-pack: `finaActiva && !(esAtencionCliente && features.includes('pos'))`. Así:
 *   - Ferretería / retail (`pos`, sin packs de servicio): ve el cockpit `/hoy` y todo el retail.
 *   - Servicios con `pos` por arrastre (restaurante con `pack_pedidos`): retail oculto, como antes.
 *   - Servicios con finas EXPLÍCITAS (peluquería con `caja`+`ventas`, sin `pos`): ve su contabilidad
 *     (Caja/Gastos/Ventas) junto a su agenda — el carril contable de servicios.
 * `esAtencionCliente` es el discriminador de familia; `resolveHomePath` elige la portada por vertical.
 */
import { createContext, useContext } from 'react'

// Meta-pack `pos` → finas (espeja core/tenancy/catalogo.META_PACKS). El backend expande en /config;
// esta re-expansión es fail-safe (tests, cachés viejos durante un deploy).
const META_POS = ['ventas', 'caja', 'inventario']

// Meta-pack `construccion` → finas (espeja core/tenancy/catalogo.META_PACKS del vertical construcción).
// Igual que `pos`, el backend lo expande en /config; esta re-expansión es fail-safe (tests, cachés
// viejos durante un deploy). Habilita las rutas de obra/maquinaria/herramientas/nómina del vertical.
const META_CONSTRUCCION = ['obras', 'maquinaria', 'herramientas', 'cotizaciones_aiu', 'nomina', 'cartera_alquiler', 'resbalos']

// Expande los meta-packs conocidos a sus features finas antes de gatear. Fail-safe: si el /config ya
// vino expandido (caso normal), el Set evita duplicados y el resultado es idéntico.
function expandirMetapacks(features = []) {
  let feats = features
  if (feats.includes('pos')) feats = [...new Set([...feats, ...META_POS])]
  if (feats.includes('construccion')) feats = [...new Set([...feats, ...META_CONSTRUCCION])]
  return feats
}

// Packs de SERVICIO (atención a cliente): el discriminador de FAMILIA de dashboard (ADR 0018). Un tenant
// con cualquiera de estos es de servicios —aunque tenga `pos` por dependencia (p. ej. el menú de un
// restaurante reusa el catálogo POS)— y por tanto NO ve el dashboard de retail.
const PACKS_ATENCION_CLIENTE = ['pack_agenda', 'pack_pedidos', 'pack_reservas']

// Rutas RETAIL/CONTABLES. Cada una se gatea por su feature FINA (ADR 0021) con la regla de supresión
// de familia (ver isRouteEnabled). `/historial` NO está aquí: es transversal a las dos familias
// (ventas en POS, pedidos/citas/reservas en servicios) y lleva su propia condición en isRouteEnabled.
const RUTAS_RETAIL = new Set([
  '/hoy', '/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
  '/top-productos', '/kardex', '/devoluciones',
])

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → siempre visibles
// (hoy: /clientes, /resultados). `/inicio` es la portada de servicios: se resuelve aparte
// (resolveHomePath), no por inclusión de una feature. Las rutas de RUTAS_RETAIL llevan además la
// regla de supresión de familia (ADR 0021 §D6) en isRouteEnabled.
export const RUTA_FEATURE = {
  // Contable/retail por feature fina (ADR 0021). El cockpit `/hoy` es la experiencia integrada de
  // ferretería: sigue siendo del meta-pack `pos`.
  '/hoy': 'pos',
  '/ventas': 'ventas',
  '/caja': 'caja',
  '/gastos': 'caja',
  '/inventario': 'inventario',
  '/compras': 'inventario',
  '/proveedores': 'inventario',
  '/kardex': 'inventario',
  '/devoluciones': 'ventas',
  '/top-productos': 'ventas',
  // Vertical construcción (Fase 1 PIM + Ola A): cada tab por su feature fina. NO son RUTAS_RETAIL (no
  // llevan la supresión de familia): gate simple `feats.includes(requerida)`.
  '/cotizaciones-obra': 'cotizaciones_aiu',
  '/obras': 'obras',
  '/maquinas': 'maquinaria',
  '/herramientas': 'herramientas',
  '/trabajadores': 'nomina',
  '/nomina': 'nomina',
  // Resbalos + análisis de precios de proveedor (Fase 8): reportes del vertical; flag fina `resbalos`.
  '/resbalos': 'resbalos',
  // `/historial` es transversal (POS y servicios) → condición propia en isRouteEnabled, no aquí.
  // Fiscal
  '/facturacion': 'facturacion_electronica',
  // Facturas recibidas por QR (ADR 0020): reusa la capa RADIAN de compras fiscal → gate `compras_fiscal`.
  '/facturas-recibidas': 'compras_fiscal',
  '/libro-iva': 'libro_iva',
  '/libros': 'libros_contables',
  '/estados-financieros': 'contabilidad_ledger',
  '/retenciones': 'retenciones',
  '/conciliacion': 'conciliacion_bancaria',
  '/compras-fiscal': 'compras_fiscal',
  // Packs de servicios
  '/agenda': 'pack_agenda',
  '/conversaciones': 'canal_whatsapp',
  '/conocimiento': 'pack_faq',
  '/cartera': 'pack_cobranza',
  '/cuentas-por-pagar': 'pack_pagar',
  '/pedidos': 'pack_pedidos',
  '/cotizaciones': 'pack_ventas',
  '/postventa': 'pack_postventa',
  '/cobros': 'pagos_online',
  '/reservas': 'pack_reservas',
}

/**
 * ¿El tenant es de la familia "atención a cliente" (servicios)? True si activa algún pack de servicio
 * (agenda/pedidos/reservas). Es el discriminador de FAMILIA de dashboard del ADR 0018: separa la
 * ferretería (retail) del agente de servicios, incluso cuando este arrastra `pos` por dependencia.
 */
export function esAtencionCliente(features = []) {
  return PACKS_ATENCION_CLIENTE.some((pack) => features.includes(pack))
}

/**
 * Portada del tenant según su vertical (sin hardcodear slug), ADR 0018:
 *   - `pack_pedidos`             → `/pedidos` (comandera del restaurante: su home operativa).
 *   - `pack_agenda`/`pack_reservas` → `/inicio` (home del agente: citas, pendientes, KPIs).
 *   - `pos` (y nada de lo anterior) → `/hoy` (cockpit POS de ferretería, intacto).
 *   - resto                       → `/inicio` (núcleo de servicio).
 */
export function resolveHomePath(features = []) {
  if (features.includes('pack_pedidos')) return '/pedidos'
  if (features.includes('pack_agenda') || features.includes('pack_reservas')) return '/inicio'
  if (features.includes('pos')) return '/hoy'
  return '/inicio'
}

/** ¿La ruta está habilitada según las features efectivas? Núcleo (sin requisito) → siempre true. */
export function isRouteEnabled(path, features = []) {
  const feats = expandirMetapacks(features)
  // Las dos portadas son excluyentes: solo la portada resuelta queda visible en el nav.
  if (path === '/inicio') return resolveHomePath(feats) === '/inicio'
  // `/historial` es transversal a las dos familias (ADR 0018): quien registra ventas ve su historial
  // y la familia de servicios el suyo por vertical (pedidos/citas/reservas).
  if (path === '/historial') return feats.includes('ventas') || esAtencionCliente(feats)
  // Retail/contable (ADR 0021 §D6): feature fina activa, salvo el arrastre histórico del meta-pack
  // en tenants de servicios (restaurante con `pos` por dependencia NO ve caja/kárdex de ferretería).
  if (RUTAS_RETAIL.has(path)) {
    const finaActiva = feats.includes(RUTA_FEATURE[path])
    return finaActiva && !(esAtencionCliente(feats) && feats.includes('pos'))
  }
  const requerida = RUTA_FEATURE[path]
  return !requerida || feats.includes(requerida)
}

const FeaturesContext = createContext([])

export function FeaturesProvider({ features = [], children }) {
  return <FeaturesContext.Provider value={features}>{children}</FeaturesContext.Provider>
}

export function useFeatures() {
  return useContext(FeaturesContext)
}
