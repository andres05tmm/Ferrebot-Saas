/*
 * Rebote a la landing cuando no hay sesión (plan §3, M4). Complementa ProtectedRoute.test.jsx (que
 * cubre el caso dev sin landing → /login propio). Aquí se controla el host para verificar el `next`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ProtectedRoute from './ProtectedRoute.jsx'

const realLocation = window.location

// Sustituye window.location por un doble con el host deseado + replace espiable (jsdom no deja
// reasignar hostname directo). handoffNav usa window.location.replace; currentHostname lee hostname.
function setHost(hostname) {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...realLocation, hostname, replace: vi.fn() },
  })
}

function renderApp() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<ProtectedRoute><div>CONTENIDO PRIVADO</div></ProtectedRoute>} />
        <Route path="/login" element={<div>PANTALLA LOGIN</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
})

describe('ProtectedRoute — rebote a la landing', () => {
  it('sin sesión y con landing configurada rebota con next = slug del host', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')
    setHost('barberia-demo.melquiadez.com')

    renderApp()

    expect(window.location.replace).toHaveBeenCalledWith(
      'https://melquiadez.com/login?next=barberia-demo',
    )
    expect(screen.queryByText('CONTENIDO PRIVADO')).toBeNull()
    expect(screen.queryByText('PANTALLA LOGIN')).toBeNull()   // en prod NO usa el /login propio
  })

  it('app.melquiadez.com (sin slug) rebota a la landing SIN next', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')
    setHost('app.melquiadez.com')

    renderApp()

    expect(window.location.replace).toHaveBeenCalledWith('https://melquiadez.com/login')
  })

  it('sin landing configurada (dev) cae al /login propio del dashboard', () => {
    setHost('localhost')

    renderApp()

    expect(screen.getByText('PANTALLA LOGIN')).toBeInTheDocument()
  })

  it('con sesión renderiza el hijo (no rebota)', () => {
    vi.stubEnv('VITE_LANDING_ORIGIN', 'https://melquiadez.com')
    vi.stubEnv('VITE_BASE_DOMAIN', 'melquiadez.com')
    localStorage.setItem('ferrebot_token', 'jwt')
    setHost('barberia-demo.melquiadez.com')

    renderApp()

    expect(screen.getByText('CONTENIDO PRIVADO')).toBeInTheDocument()
    expect(window.location.replace).not.toHaveBeenCalled()
  })
})
