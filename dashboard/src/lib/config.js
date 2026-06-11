/*
 * config.js — arranque del dashboard (bootstrap white-label).
 *
 * Trae GET /config y:
 *   (1) aplica el theming → inyecta --color-primary y el `data-tema` del tenant (white-label),
 *   (2) devuelve { features, branding, usuario } para el gating de navegación.
 *
 * Corre SOLO autenticado (detrás de ProtectedRoute). El 401 lo maneja api.js (limpia sesión y va a
 * /login), así que aquí NO se traga el error: se propaga para que el shell muestre un estado de error
 * real (red caída). El default de color solo aplica si el branding no trae color_primario.
 */
import { apiJson } from './api.js'

export const COLOR_PRIMARY_DEFAULT = '#C8200E'

export function applyTheming(branding) {
  const root = document.documentElement
  const color = branding?.color_primario || COLOR_PRIMARY_DEFAULT
  root.style.setProperty('--color-primary', color)

  // Tema de UI con nombre (p. ej. "aurora"): activa el bloque de CSS vars [data-tema] en <html>.
  // Sin tema (null/"base") → se quita el atributo y manda el tema base (rojo de siempre). El light/dark
  // lo sigue gobernando data-theme (AppShell); aquí solo elegimos la PALETA/forma del tenant.
  const tema = (branding?.tema || '').trim().toLowerCase()
  if (tema && tema !== 'base') {
    root.setAttribute('data-tema', tema)
  } else {
    root.removeAttribute('data-tema')
  }
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
