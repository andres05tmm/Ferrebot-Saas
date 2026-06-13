/*
 * features.jsx — gating de navegación por capacidades de la empresa.
 *
 * Las features vienen de GET /config con los NOMBRES CANÓNICOS de core/tenancy/catalogo.py. El núcleo
 * (clientes, reportes) siempre llega, así que sus rutas quedan visibles; el resto (POS, fiscal, packs
 * de servicios) solo si su capacidad está activa.
 *
 * ADR 0008: el POS dejó de ser núcleo; el retail vive tras el pack `pos`.
 *
 * ADR 0018 — DOS FAMILIAS de dashboard. El flag `pos` ya no basta para decidir qué portada y qué tabs
 * ve un tenant, porque packs de servicio reusan el catálogo POS (un restaurante con `pack_pedidos`
 * tiene `pos` por dependencia, pero NO es una ferretería). Discriminamos por FAMILIA:
 *   - Ferretería / retail: tiene `pos` y NINGÚN pack de atención a cliente. Ve el cockpit `/hoy`, las
 *     rutas de retail (ventas, caja, inventario, compras, proveedores, gastos) y los reportes POS
 *     (top-productos, kárdex, historial).
 *   - Atención a cliente / servicios: tiene algún pack de servicio (`pack_agenda`, `pack_pedidos`,
 *     `pack_reservas`). Su portada es la del agente (`/inicio` o `/pedidos`) y NO ve el retail aunque
 *     arrastre `pos` por dependencia.
 * `esAtencionCliente` es el discriminador de familia; `resolveHomePath` elige la portada por vertical.
 */
import { createContext, useContext } from 'react'

// Packs de SERVICIO (atención a cliente): el discriminador de FAMILIA de dashboard (ADR 0018). Un tenant
// con cualquiera de estos es de servicios —aunque tenga `pos` por dependencia (p. ej. el menú de un
// restaurante reusa el catálogo POS)— y por tanto NO ve el dashboard de retail.
const PACKS_ATENCION_CLIENTE = ['pack_agenda', 'pack_pedidos', 'pack_reservas']

// Rutas RETAIL/CONTABLES: la familia ferretería. Visibles solo con `pos` Y sin packs de atención a
// cliente (ADR 0018). Incluye la portada POS `/hoy` y los reportes POS-específicos.
const RUTAS_RETAIL = new Set([
  '/hoy', '/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
  '/top-productos', '/kardex', '/historial',
])

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → siempre visibles
// (hoy: /clientes, /resultados). `/inicio` es la portada de servicios: se resuelve aparte
// (resolveHomePath), no por inclusión de una feature. Las rutas de RUTAS_RETAIL llevan condición
// compuesta (pos Y no-atención) en isRouteEnabled; su entrada `pos` aquí queda de referencia.
export const RUTA_FEATURE = {
  // POS (pack `pos`, ADR 0008). La portada POS `/hoy` también se gatea por `pos` (Fase 1).
  '/hoy': 'pos',
  '/ventas': 'pos',
  '/caja': 'pos',
  '/inventario': 'pos',
  '/compras': 'pos',
  '/proveedores': 'pos',
  '/gastos': 'pos',
  '/top-productos': 'pos',
  '/kardex': 'pos',
  '/historial': 'pos',
  // Fiscal
  '/facturacion': 'facturacion_electronica',
  '/facturas-recibidas': 'facturacion_electronica',
  '/libro-iva': 'libro_iva',
  '/compras-fiscal': 'compras_fiscal',
  // Packs de servicios
  '/agenda': 'pack_agenda',
  '/conversaciones': 'canal_whatsapp',
  '/conocimiento': 'pack_faq',
  '/cartera': 'pack_cobranza',
  '/pedidos': 'pack_pedidos',
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
  // Las dos portadas son excluyentes: solo la portada resuelta queda visible en el nav.
  if (path === '/inicio') return resolveHomePath(features) === '/inicio'
  // Familia ferretería (ADR 0018): el retail/contable solo es visible con `pos` Y sin packs de servicio.
  if (RUTAS_RETAIL.has(path)) return features.includes('pos') && !esAtencionCliente(features)
  const requerida = RUTA_FEATURE[path]
  return !requerida || features.includes(requerida)
}

const FeaturesContext = createContext([])

export function FeaturesProvider({ features = [], children }) {
  return <FeaturesContext.Provider value={features}>{children}</FeaturesContext.Provider>
}

export function useFeatures() {
  return useContext(FeaturesContext)
}
