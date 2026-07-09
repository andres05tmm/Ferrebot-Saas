/*
 * preferencias.jsx — preferencias de UI del tenant (de GET /config), disponibles por contexto.
 *
 * Hoy: `facturarEnVenta` (default true) — si el POS auto-factura cada venta o si ofrece "Sin factura"
 * (venta interna, factura a pedido) — y `cajaObligatoria` (default false) — guard de caja del POS:
 * sin caja abierta no se cobra; el modal de apertura registra la venta pendiente. Espeja
 * BrandingProvider: default seguro sin provider (tests) → comportamiento histórico.
 */
import { createContext, useContext } from 'react'

const PreferenciasContext = createContext({ facturarEnVenta: true, cajaObligatoria: false })

export function PreferenciasProvider({ facturarEnVenta = true, cajaObligatoria = false, children }) {
  return (
    <PreferenciasContext.Provider value={{ facturarEnVenta, cajaObligatoria }}>
      {children}
    </PreferenciasContext.Provider>
  )
}

export function usePreferencias() {
  return useContext(PreferenciasContext)
}
