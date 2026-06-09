/*
 * features.jsx — gating de navegación por capacidades de la empresa.
 *
 * Las features vienen de GET /config con los NOMBRES CANÓNICOS de core/tenancy/catalogo.py. El núcleo
 * (clientes, reportes) y la home "Hoy" siempre llegan, así que sus rutas quedan visibles; el resto
 * (POS, fiscal, packs de servicios) solo si su capacidad está activa.
 *
 * ADR 0008: el POS dejó de ser núcleo. Las rutas de retail (ventas, caja, inventario, compras,
 * proveedores, gastos y los reportes POS-específicos top-productos/kárdex/historial) se gatean por
 * `pos`, para que un negocio de servicios vea un dashboard limpio.
 */
import { createContext, useContext } from 'react'

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → siempre visibles
// (hoy: /hoy, /clientes, /resultados).
export const RUTA_FEATURE = {
  // POS (pack `pos`, ADR 0008)
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
}

/** ¿La ruta está habilitada según las features efectivas? Núcleo (sin requisito) → siempre true. */
export function isRouteEnabled(path, features = []) {
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
