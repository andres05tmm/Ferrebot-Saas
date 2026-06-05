/*
 * features.jsx — gating de navegación por capacidades de la empresa.
 *
 * Las features vienen de GET /config con los NOMBRES CANÓNICOS de core/tenancy/catalogo.py. El
 * núcleo (ventas, inventario, caja, gastos, clientes, proveedores, reportes) siempre llega en
 * /config, así que las rutas núcleo quedan visibles; las fiscales solo si su capacidad está activa.
 */
import { createContext, useContext } from 'react'

// Ruta → capacidad requerida (catalogo.py). Las rutas NO listadas son núcleo → siempre visibles.
export const RUTA_FEATURE = {
  '/facturacion': 'facturacion_electronica',
  '/facturas-recibidas': 'facturacion_electronica',
  '/libro-iva': 'libro_iva',
  '/compras-fiscal': 'compras_fiscal',
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
