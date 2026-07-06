import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import { BrandingProvider } from '../lib/branding.jsx'
import PwaInstall from './PwaInstall.jsx'

function dispararPrompt() {
  const evt = new Event('beforeinstallprompt')
  evt.preventDefault = vi.fn()
  evt.prompt = vi.fn()
  evt.userChoice = Promise.resolve({ outcome: 'accepted' })
  act(() => { window.dispatchEvent(evt) })
  return evt
}

beforeEach(() => {
  localStorage.clear()
  // matchMedia: en jsdom no existe; simula "no instalada aún".
  window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('PwaInstall', () => {
  it('no muestra nada hasta que llega beforeinstallprompt', () => {
    render(<BrandingProvider branding={{ nombre_comercial: 'Punto Rojo' }}><PwaInstall /></BrandingProvider>)
    expect(screen.queryByRole('dialog', { name: /instalar/i })).toBeNull()
  })

  it('al recibir el evento muestra el banner con el nombre del tenant', () => {
    render(<BrandingProvider branding={{ nombre_comercial: 'Punto Rojo' }}><PwaInstall /></BrandingProvider>)
    const evt = dispararPrompt()
    expect(evt.preventDefault).toHaveBeenCalled()               // no dejamos el mini-infobar nativo
    expect(screen.getByText('Instalar Punto Rojo')).toBeInTheDocument()
  })

  it('el botón Instalar dispara el prompt nativo', async () => {
    render(<BrandingProvider branding={{ nombre_comercial: 'Punto Rojo' }}><PwaInstall /></BrandingProvider>)
    const evt = dispararPrompt()
    await act(async () => { fireEvent.click(screen.getByText('Instalar')) })
    expect(evt.prompt).toHaveBeenCalled()
  })

  it('"Ahora no" oculta el banner y persiste la decisión', () => {
    render(<BrandingProvider branding={{ nombre_comercial: 'Punto Rojo' }}><PwaInstall /></BrandingProvider>)
    dispararPrompt()
    fireEvent.click(screen.getByText('Ahora no'))
    expect(screen.queryByText('Instalar Punto Rojo')).toBeNull()
    expect(localStorage.getItem('ferrebot_pwa_install_dismissed')).toBe('1')
  })

  it('no reaparece si ya fue descartado antes', () => {
    localStorage.setItem('ferrebot_pwa_install_dismissed', '1')
    render(<BrandingProvider branding={{ nombre_comercial: 'Punto Rojo' }}><PwaInstall /></BrandingProvider>)
    dispararPrompt()
    expect(screen.queryByText('Instalar Punto Rojo')).toBeNull()
  })
})
