import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyTheming, bootConfig, COLOR_PRIMARY_DEFAULT, DEFAULT_DISPLAY_FONT } from './config.js'

const BRAND_VARS = [
  '--color-primary', '--color-primary-up', '--color-surface', '--color-card', '--color-line',
  '--color-ink', '--color-ink-soft', '--color-ok', '--color-warn', '--color-bad',
  '--radius-brand', '--font-display', '--font-ui',
]

afterEach(() => {
  vi.restoreAllMocks()
  for (const v of BRAND_VARS) document.documentElement.style.removeProperty(v)
  document.documentElement.removeAttribute('data-tema')
  document.querySelectorAll('link[id^="mq-font-"]').forEach((l) => l.remove())
})

// Tokens resueltos del preset navaja (espejan core/tenancy/branding_presets.PRESETS['navaja']).
const NAVAJA = {
  primario: '#d99a3d', primario_up: '#e8b066', superficie: '#171310', card: '#211c17',
  linea: '#352d24', tinta: '#f0e9df', tinta_suave: '#a59a8a',
  ok: '#7fb069', warn: '#e6a23c', bad: '#d9534f',
  radius: '14px', font_display: 'Archivo', font_ui: 'Archivo',
}

describe('boot theming', () => {
  it('inyecta --color-primary desde branding.color_primario y expone features', async () => {
    const config = {
      features: ['ventas', 'facturacion_electronica'],
      branding: { color_primario: '#0d6efd' },
      usuario: { id: 1, rol: 'admin', tenant: 'pr' },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => config }))

    const result = await bootConfig()

    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe('#0d6efd')
    expect(result.features).toEqual(['ventas', 'facturacion_electronica'])
  })

  it('propaga el error si /config falla (no traga el fallo; el shell muestra error)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))
    await expect(bootConfig()).rejects.toThrow('HTTP 500')
  })

  it('applyTheming usa el default cuando no hay branding', () => {
    applyTheming(null)
    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(COLOR_PRIMARY_DEFAULT)
    expect(document.documentElement.hasAttribute('data-tema')).toBe(false)
  })

  it('applyTheming setea data-tema desde branding.tema (white-label)', () => {
    applyTheming({ color_primario: '#0EA5A4', tema: 'aurora' })
    expect(document.documentElement.getAttribute('data-tema')).toBe('aurora')
  })

  it('applyTheming no setea data-tema sin tema (o "base") → tema base rojo', () => {
    document.documentElement.setAttribute('data-tema', 'aurora')  // estado previo
    applyTheming({ color_primario: '#C8200E', tema: 'base' })
    expect(document.documentElement.hasAttribute('data-tema')).toBe(false)
    applyTheming({ color_primario: '#C8200E' })                   // tema ausente
    expect(document.documentElement.hasAttribute('data-tema')).toBe(false)
  })
})

describe('theming por preset (tokens planos de /config)', () => {
  it('aplica el set completo de variables CSS desde branding.tokens (preset navaja)', () => {
    applyTheming({ color_primario: '#d99a3d', preset: 'navaja', tokens: NAVAJA })
    const s = document.documentElement.style

    expect(s.getPropertyValue('--color-primary')).toBe('#d99a3d')
    expect(s.getPropertyValue('--color-primary-up')).toBe('#e8b066')
    expect(s.getPropertyValue('--color-surface')).toBe('#171310')
    expect(s.getPropertyValue('--color-card')).toBe('#211c17')
    expect(s.getPropertyValue('--color-line')).toBe('#352d24')
    expect(s.getPropertyValue('--color-ink')).toBe('#f0e9df')
    expect(s.getPropertyValue('--color-ink-soft')).toBe('#a59a8a')
    expect(s.getPropertyValue('--radius-brand')).toBe('14px')
    // La display font entra al stack y el preset activa su bloque [data-tema].
    expect(s.getPropertyValue('--font-display')).toContain('Archivo')
    expect(document.documentElement.getAttribute('data-tema')).toBe('navaja')
  })

  it('carga la fuente display dinámica solo si difiere de la default (Inter)', () => {
    applyTheming({ preset: 'navaja', tokens: NAVAJA })
    const link = document.getElementById('mq-font-archivo')
    expect(link).not.toBeNull()
    expect(link.href).toContain('family=Archivo')

    // Un preset cuya display ES la default (Inter) NO inyecta ningún link.
    document.querySelectorAll('link[id^="mq-font-"]').forEach((l) => l.remove())
    applyTheming({ preset: 'lienzo', tokens: { font_display: DEFAULT_DISPLAY_FONT } })
    expect(document.querySelectorAll('link[id^="mq-font-"]').length).toBe(0)
  })

  it('fallback: sin tokens no rompe (solo --color-primary, sin variables de paleta)', () => {
    applyTheming({ color_primario: '#0d6efd' })   // /config viejo: branding sin tokens
    const s = document.documentElement.style
    expect(s.getPropertyValue('--color-primary')).toBe('#0d6efd')
    // Las variables de paleta NO se tocan inline (quedan en su fallback del :root del index.css).
    expect(s.getPropertyValue('--color-surface')).toBe('')
    expect(s.getPropertyValue('--font-display')).toBe('')
    expect(document.querySelectorAll('link[id^="mq-font-"]').length).toBe(0)
  })
})
