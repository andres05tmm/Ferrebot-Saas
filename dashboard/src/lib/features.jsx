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

// FAMILIA CONSTRUCCIÓN (vertical PIM): tercera familia de dashboard, junto a retail y servicios. Un
// tenant es de construcción si tiene el vertical (`construccion` o su feature núcleo `obras`), aunque
// arrastre `pos` por la dependencia de `inventario` (materiales/compras de obra). Como los de servicios
// respecto al retail, NO ve el cockpit ni la venta de mostrador de la ferretería: su operación es la obra.
const FEATURES_CONSTRUCCION = ['construccion', 'obras']

// Rutas de RETAIL PURO (venta de mostrador): no tienen sentido para una constructora ni para servicios.
// Se suprimen para la familia construcción (ver isRouteEnabled). NO incluye caja/inventario/compras/
// gastos: esos son operación compartida (una obra maneja caja, materiales, compras y gastos).
const RUTAS_RETAIL_PURO = new Set(['/hoy', '/ventas', '/devoluciones', '/top-productos', '/kardex'])

// Rutas RETAIL/CONTABLES. Cada una se gatea por su feature FINA (ADR 0021) con la regla de supresión
// de familia (ver isRouteEnabled). `/historial` NO está aquí: es transversal a las dos familias
// (ventas en POS, pedidos/citas/reservas en servicios) y lleva su propia condición en isRouteEnabled.
const RUTAS_RETAIL = new Set([
  '/hoy', '/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
  '/top-productos', '/kardex', '/devoluciones',
])

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → visibles por defecto
// (hoy: /clientes siempre; /resultados salvo en construcción — ver isRouteEnabled). `/inicio` es la
// portada de servicios: se resuelve aparte (resolveHomePath), no por inclusión de una feature. Las
// rutas de RUTAS_RETAIL llevan además la regla de supresión de familia (ADR 0021 §D6) en isRouteEnabled.
export const RUTA_FEATURE = {
  // Contable/retail por feature fina (ADR 0021). El cockpit `/hoy` es la experiencia integrada de
  // ferretería: sigue siendo del meta-pack `pos`.
  '/hoy': 'pos',
  '/ventas': 'ventas',
  '/caja': 'caja',
  '/gastos': 'caja',
  '/inventario': 'inventario',
  '/compras': 'inventario',
  '/pedidos-proveedor': 'pedidos_proveedor',
  '/proveedores': 'inventario',
  '/kardex': 'inventario',
  '/devoluciones': 'ventas',
  '/top-productos': 'ventas',
  // Vertical construcción (Fase 1 PIM + Ola A): cada tab por su feature fina. NO son RUTAS_RETAIL (no
  // llevan la supresión de familia): gate simple `feats.includes(requerida)`.
  // `/panel` (cockpit del dueño, F3): portada de la familia construcción. Cuelga de `obras` como el
  // resto del vertical, pero lleva además la restricción de FAMILIA (solo construcción) en isRouteEnabled.
  '/panel': 'obras',
  '/cotizaciones-obra': 'cotizaciones_aiu',
  '/obras': 'obras',
  // Calendario de obra (Commit 3 PIM): actividad diaria del vertical. Gate simple por `obras` (no
  // RUTAS_RETAIL → sin supresión de familia), coherente con /obras y el resto del vertical.
  '/calendario': 'obras',
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
 * ¿El tenant es de la familia "construcción" (vertical PIM)? True si tiene el vertical (`construccion`
 * o su núcleo `obras`). Es el discriminador de familia análogo a `esAtencionCliente`: separa la obra
 * civil del retail, aun cuando arrastra `pos` por la dependencia de `inventario`.
 */
export function esConstruccion(features = []) {
  return FEATURES_CONSTRUCCION.some((f) => features.includes(f))
}

/**
 * Portada del tenant según su vertical (sin hardcodear slug), ADR 0018. Las FAMILIAS se evalúan en
 * orden de especificidad; construcción y servicios van ANTES que `pos` porque ambos lo arrastran por
 * dependencia (inventario / catálogo) sin ser retail:
 *   - `pack_pedidos`             → `/pedidos` (comandera del restaurante: su home operativa).
 *   - `pack_agenda`/`pack_reservas` → `/inicio` (home del agente: citas, pendientes, KPIs).
 *   - construcción (`obras`)     → `/panel` (cockpit del dueño) si admin; `/obras` (vista operativa)
 *                                   si vendedor. RBAC: el cockpit expone cifras financieras del mes, así
 *                                   que el vendedor aterriza en la operación. Sin `rol` (nav interno) el
 *                                   default es `/obras`, el más seguro (nunca expone finanzas por defecto).
 *   - `pos` (y nada de lo anterior) → `/hoy` (cockpit POS de ferretería, intacto).
 *   - resto                       → `/inicio` (núcleo de servicio).
 */
export function resolveHomePath(features = [], rol = null) {
  const feats = expandirMetapacks(features)
  if (feats.includes('pack_pedidos')) return '/pedidos'
  if (feats.includes('pack_agenda') || feats.includes('pack_reservas')) return '/inicio'
  if (esConstruccion(feats)) {
    const esAdmin = rol === 'admin' || rol === 'super_admin'
    return esAdmin ? '/panel' : '/obras'
  }
  if (feats.includes('pos')) return '/hoy'
  return '/inicio'
}

/** ¿La ruta está habilitada según las features efectivas? Núcleo (sin requisito) → siempre true. */
export function isRouteEnabled(path, features = []) {
  const feats = expandirMetapacks(features)
  // Familia construcción: suprime el RETAIL PURO (cockpit `/hoy` + venta de mostrador). Una constructora
  // arrastra `pos` por `inventario`, pero no vende tickets: conserva caja/inventario/compras/gastos.
  if (esConstruccion(feats) && RUTAS_RETAIL_PURO.has(path)) return false
  // `/panel` (cockpit del dueño, F3): portada EXCLUSIVA de la familia construcción. Aunque cuelga de
  // `obras`, se restringe por FAMILIA para que ningún otro vertical la vea (portadas top mutuamente
  // excluyentes). El RBAC admin-only lo aplican el guard del panel y resolveHomePath(rol), no el nav.
  if (path === '/panel') return esConstruccion(feats)
  // Las portadas son mutuamente excluyentes: solo la portada resuelta (resolveHomePath) queda en el nav.
  if (path === '/inicio') return resolveHomePath(feats) === '/inicio'
  // `/historial` es transversal a retail/servicios (ADR 0018): quien registra ventas ve su historial y
  // la familia de servicios el suyo por vertical. La constructora NO (su traza vive en obras/nómina).
  if (path === '/historial') return !esConstruccion(feats) && (feats.includes('ventas') || esAtencionCliente(feats))
  // `/resultados` es núcleo (P&L) para retail y servicios, pero su cálculo lee SOLO ventas POS. Una
  // constructora no vende por mostrador (su ingreso es alquiler/resbalos/facturas de obra), así que ese
  // P&L le mostraría una "pérdida perpetua" falsa; el cockpit `/panel` ya le da la foto financiera real.
  // Se suprime por familia, igual que `/historial`. BACKLOG: reabrir el P&L de obra cuando el contable
  // multi-vertical sirva los ingresos de obra (facturas de obra + resbalos), no solo las ventas POS.
  if (path === '/resultados') return !esConstruccion(feats)
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
