/*
 * branding.jsx — marca white-label de la empresa (de GET /config) disponible por contexto.
 *
 * `logo_url` + `nombre_comercial` los consume el shell (sidebar) para mostrar la marca de cada empresa
 * (p. ej. el logo + "Punto Rojo"). El color primario lo aplica el theming (lib/config.js). Default {} →
 * sin BrandingProvider (p. ej. en tests) el shell usa su fallback neutro sin romperse.
 */
import { createContext, useContext } from 'react'

const BrandingContext = createContext({})

export function BrandingProvider({ branding = {}, children }) {
  return <BrandingContext.Provider value={branding || {}}>{children}</BrandingContext.Provider>
}

export function useBranding() {
  return useContext(BrandingContext)
}
