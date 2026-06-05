import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyTheming, bootConfig, COLOR_PRIMARY_DEFAULT } from './config.js'

afterEach(() => {
  vi.restoreAllMocks()
  document.documentElement.style.removeProperty('--color-primary')
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

  it('cae al color por defecto si /config falla', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

    const result = await bootConfig()

    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(COLOR_PRIMARY_DEFAULT)
    expect(result.features).toEqual([])
  })

  it('applyTheming usa el default cuando no hay branding', () => {
    applyTheming(null)
    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(COLOR_PRIMARY_DEFAULT)
  })
})
