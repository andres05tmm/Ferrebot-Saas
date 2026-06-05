/*
 * config.js — arranque del dashboard (bootstrap white-label).
 *
 * Trae GET /config y:
 *   (1) aplica el theming → inyecta --color-primary en :root desde branding.color_primario,
 *   (2) devuelve { features, branding, usuario } para el gating de navegación.
 *
 * Corre SOLO autenticado (detrás de ProtectedRoute). El 401 lo maneja api.js (limpia sesión y va a
 * /login), así que aquí NO se traga el error: se propaga para que el shell muestre un estado de error
 * real (red caída). El default de color solo aplica si el branding no trae color_primario.
 */
import { apiJson } from './api.js'

export const COLOR_PRIMARY_DEFAULT = '#C8200E'

export function applyTheming(branding) {
  const color = branding?.color_primario || COLOR_PRIMARY_DEFAULT
  document.documentElement.style.setProperty('--color-primary', color)
}

export async function bootConfig() {
  const config = await apiJson('/config')
  applyTheming(config.branding)
  return {
    features: config.features || [],
    branding: config.branding || { color_primario: COLOR_PRIMARY_DEFAULT },
    usuario: config.usuario || null,
  }
}
