import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyTheming, bootConfig, COLOR_PRIMARY_DEFAULT } from './config.js'

afterEach(() => {
  vi.restoreAllMocks()
  document.documentElement.style.removeProperty('--color-primary')
  document.documentElement.removeAttribute('data-tema')
})

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
