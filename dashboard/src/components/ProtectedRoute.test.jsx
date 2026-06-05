import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ProtectedRoute from './ProtectedRoute.jsx'

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
afterEach(() => cleanup())

describe('ProtectedRoute', () => {
  it('sin token redirige a /login', () => {
    renderApp()
    expect(screen.getByText('PANTALLA LOGIN')).toBeInTheDocument()
    expect(screen.queryByText('CONTENIDO PRIVADO')).toBeNull()
  })

  it('con token renderiza el hijo', () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    renderApp()
    expect(screen.getByText('CONTENIDO PRIVADO')).toBeInTheDocument()
  })
})
