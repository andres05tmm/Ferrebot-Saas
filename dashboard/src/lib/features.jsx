/*
 * features.jsx — gating de navegación por capacidades de la empresa.
 *
 * Las features vienen de GET /config con los NOMBRES CANÓNICOS de core/tenancy/catalogo.py. El núcleo
 * (clientes, reportes) siempre llega, así que sus rutas quedan visibles; el resto (POS, fiscal, packs
 * de servicios) solo si su capacidad está activa.
 *
 * ADR 0008: el POS dejó de ser núcleo. Las rutas de retail (ventas, caja, inventario, compras,
 * proveedores, gastos y los reportes POS-específicos top-productos/kárdex/historial) se gatean por
 * `pos`, para que un negocio de servicios vea un dashboard limpio.
 *
 * Fase 1 (home de agente): la PORTADA dejó de ser fija a "Hoy" (POS). Hay dos portadas mutuamente
 * excluyentes — `/hoy` (cockpit POS, requiere `pos`) y `/inicio` (home del agente de servicios) — y
 * `resolveHomePath` decide cuál es la portada del tenant. El nav muestra solo la que aplica.
 */
import { createContext, useContext } from 'react'

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → siempre visibles
// (hoy: /clientes, /resultados). `/inicio` es la portada de servicios: se resuelve aparte
// (resolveHomePath), no por inclusión de una feature.
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
 * Portada del tenant según sus features (sin hardcodear slug):
 *   - con `pos`            → `/hoy` (cockpit POS de ferretería, intacto).
 *   - servicios sin `pos`  → `/inicio` (home del agente: citas, pendientes, KPIs).
 * El núcleo de servicio (cualquier tenant sin `pos`) aterriza siempre en `/inicio`.
 */
export function resolveHomePath(features = []) {
  return features.includes('pos') ? '/hoy' : '/inicio'
}

/** ¿La ruta está habilitada según las features efectivas? Núcleo (sin requisito) → siempre true. */
export function isRouteEnabled(path, features = []) {
  // Las dos portadas son excluyentes: solo la portada resuelta queda visible en el nav.
  if (path === '/inicio') return resolveHomePath(features) === '/inicio'
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
