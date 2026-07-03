/*
 * config.js — arranque del dashboard (bootstrap white-label).
 *
 * Trae GET /config y:
 *   (1) aplica el theming → inyecta el set COMPLETO de tokens de marca (paleta + radio + fuentes) como
 *       variables CSS, activa el `data-tema` del preset y carga la fuente display si no es la default,
 *   (2) devuelve { features, branding, usuario } para el gating de navegación.
 *
 * El branding viaja YA resuelto desde /config (`branding.tokens`, planos): aquí no se interpreta el
 * preset, solo se aplican sus tokens. Con fallbacks: un /config viejo SIN tokens no rompe nada (cae al
 * comportamiento de antes: solo --color-primary). Corre SOLO autenticado (detrás de ProtectedRoute).
 */
import { apiJson } from './api'

// Default pre-/sin-branding = oro Melquiadez (la plataforma), no el rojo de un tenant. Un tenant
// (p. ej. Punto Rojo) recupera su color porque viaja como color_primario explícito en /config.
export const COLOR_PRIMARY_DEFAULT = '#b8924f'
// Fuente display que el dashboard ya trae en su stack base: si el preset la usa, no hay que cargar nada.
export const DEFAULT_DISPLAY_FONT = 'Inter'

// token (clave de branding.tokens) → variable CSS que el shell consume. Un token ausente se omite
// (no se pisa la var base): así un /config viejo o un preset parcial degradan con gracia.
const TOKEN_A_CSS = {
  primario: '--color-primary',
  primario_up: '--color-primary-up',
  superficie: '--color-surface',
  card: '--color-card',
  linea: '--color-line',
  tinta: '--color-ink',
  tinta_suave: '--color-ink-soft',
  ok: '--color-ok',
  warn: '--color-warn',
  bad: '--color-bad',
  radius: '--radius-brand',
}

function fontStack(family) {
  return `'${family}', ui-sans-serif, system-ui, sans-serif`
}

// Carga una familia de Google Fonts una sola vez (link idempotente por id). No-op sin <head> (tests sin DOM).
function cargarFuente(family) {
  if (!family || typeof document === 'undefined' || !document.head) return
  const id = `mq-font-${family.toLowerCase().replace(/\s+/g, '-')}`
  if (document.getElementById(id)) return
  const link = document.createElement('link')
  link.id = id
  link.rel = 'stylesheet'
  const fam = family.trim().replace(/\s+/g, '+')
  link.href = `https://fonts.googleapis.com/css2?family=${fam}:wght@400;500;600;700&display=swap`
  document.head.appendChild(link)
}

export function applyTheming(branding) {
  const root = document.documentElement
  const tokens = branding?.tokens || {}

  // (1) Paleta/radio: aplica cada token presente a su variable CSS. Fallback: si no hay tokens, al menos
  // --color-primary desde color_primario (comportamiento histórico; el front viejo se ve igual).
  const primario = tokens.primario || branding?.color_primario || COLOR_PRIMARY_DEFAULT
  root.style.setProperty('--color-primary', primario)
  for (const [token, cssVar] of Object.entries(TOKEN_A_CSS)) {
    if (token === 'primario') continue
    if (tokens[token]) root.style.setProperty(cssVar, tokens[token])
  }

  // (2) Tipografía: aplica las familias y carga la display dinámica SOLO si difiere de la default ya
  // embebida (Inter). La UI font se carga igual si difiere de la default.
  if (tokens.font_display) {
    root.style.setProperty('--font-display', fontStack(tokens.font_display))
    if (tokens.font_display !== DEFAULT_DISPLAY_FONT) cargarFuente(tokens.font_display)
  }
  if (tokens.font_ui) {
    root.style.setProperty('--font-ui', fontStack(tokens.font_ui))
    if (tokens.font_ui !== DEFAULT_DISPLAY_FONT) cargarFuente(tokens.font_ui)
  }

  // (3) Preset con nombre (= `data-tema`): activa el bloque de CSS vars [data-tema="navaja"] del index.css
  // (paleta/forma completa, combinable con light/dark). Fallback a `tema` (nombre viejo). Sin preset
  // (o "base") → se quita el atributo y manda el tema base. El light/dark lo gobierna data-theme aparte.
  const preset = (branding?.preset || branding?.tema || '').trim().toLowerCase()
  if (preset && preset !== 'base') {
    root.setAttribute('data-tema', preset)
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
