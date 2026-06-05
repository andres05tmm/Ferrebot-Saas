/*
 * config.js — arranque del dashboard (bootstrap white-label).
 *
 * Trae GET /config y:
 *   (1) aplica el theming → inyecta --color-primary en :root desde branding.color_primario,
 *   (2) devuelve { features, branding, usuario } para el gating de navegación.
 *
 * Si /config falla (red caída, aún sin login en E4), arranca con defaults para no bloquear el shell.
 */
import { apiJson } from './api.js'

export const COLOR_PRIMARY_DEFAULT = '#C8200E'

export function applyTheming(branding) {
  const color = branding?.color_primario || COLOR_PRIMARY_DEFAULT
  document.documentElement.style.setProperty('--color-primary', color)
}

export async function bootConfig() {
  try {
    const config = await apiJson('/config')
    applyTheming(config.branding)
    return {
      features: config.features || [],
      branding: config.branding || { color_primario: COLOR_PRIMARY_DEFAULT },
      usuario: config.usuario || null,
    }
  } catch {
    applyTheming(null)
    return { features: [], branding: { color_primario: COLOR_PRIMARY_DEFAULT }, usuario: null }
  }
}
