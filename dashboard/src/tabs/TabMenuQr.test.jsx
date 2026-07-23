import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import TabMenuQr from './TabMenuQr.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabMenuQr', () => {
  it('la ruta /menu-qr se gatea por menu_qr', () => {
    expect(isRouteEnabled('/menu-qr', ['ventas'])).toBe(false)
    expect(isRouteEnabled('/menu-qr', ['menu_qr', 'ventas'])).toBe(true)
  })

  it('pinta el QR (SVG del backend) y la URL pública', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, status: 200,
      json: async () => ({ url: 'https://resto.melquiadez.com/publico/resto/menu', svg: '<svg data-testid="qr"></svg>' }),
    })))
    render(<MemoryRouter><TabMenuQr /></MemoryRouter>)
    expect(await screen.findByText('https://resto.melquiadez.com/publico/resto/menu')).toBeInTheDocument()
    expect(screen.getByTestId('qr')).toBeInTheDocument()
  })
})
