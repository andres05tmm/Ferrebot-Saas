import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import PlatformRoute from './PlatformRoute.jsx'

function renderEnAdmin() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<PlatformRoute><div>PANEL ADMIN</div></PlatformRoute>} />
        <Route path="/" element={<div>HOME TENANT</div>} />
        <Route path="/login" element={<div>LOGIN</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => cleanup())

describe('PlatformRoute (gate del panel super-admin)', () => {
  it('un super_admin ve el panel', () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    localStorage.setItem('ferrebot_user', JSON.stringify({ id: 0, rol: 'super_admin', tenant: null }))
    renderEnAdmin()
    expect(screen.getByText('PANEL ADMIN')).toBeInTheDocument()
  })

  it('un usuario NORMAL (admin) NO accede: lo saca a su dashboard', () => {
    localStorage.setItem('ferrebot_token', 'jwt')
    localStorage.setItem('ferrebot_user', JSON.stringify({ id: 5, rol: 'admin', tenant: 'pr' }))
    renderEnAdmin()
    expect(screen.queryByText('PANEL ADMIN')).toBeNull()
    expect(screen.getByText('HOME TENANT')).toBeInTheDocument()
  })

  it('sin sesión → /login', () => {
    renderEnAdmin()
    expect(screen.queryByText('PANEL ADMIN')).toBeNull()
    expect(screen.getByText('LOGIN')).toBeInTheDocument()
  })
})
