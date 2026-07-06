/*
 * preferencias.jsx — preferencias de UI del tenant (de GET /config), disponibles por contexto.
 *
 * Hoy: `facturarEnVenta` (default true) — si el POS auto-factura cada venta o si ofrece "Sin factura"
 * (venta interna, factura a pedido). Espeja BrandingProvider: default seguro sin provider (tests) → el
 * POS cae al comportamiento histórico (auto-facturar).
 */
import { createContext, useContext } from 'react'

const PreferenciasContext = createContext({ facturarEnVenta: true })

export function PreferenciasProvider({ facturarEnVenta = true, children }) {
  return (
    <PreferenciasContext.Provider value={{ facturarEnVenta }}>
      {children}
    </PreferenciasContext.Provider>
  )
}

export function usePreferencias() {
  return useContext(PreferenciasContext)
}
