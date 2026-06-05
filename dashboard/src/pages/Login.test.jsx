import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Login from './Login.jsx'

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>HOME OK</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

const PAYLOAD = { id: 7, first_name: 'Ana', username: 'ana', auth_date: 1700000000, hash: 'firma' }

beforeEach(() => {
  localStorage.clear()
  vi.stubEnv('VITE_TELEGRAM_BOT_USERNAME', 'elmicha_bot')
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  delete window.onTelegramAuth
})

describe('Login (Telegram Login Widget)', () => {
  it('onTelegramAuth postea a /api/v1/auth/login y en 200 guarda y navega', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ token: 'jwt-xyz', usuario: { id: 7, rol: 'admin', tenant: 'pr' } }),
    })
    vi.stubGlobal('fetch', fetchMock)

    renderLogin()
    await act(async () => { await window.onTelegramAuth(PAYLOAD) })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/login')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toMatchObject({ id: 7, hash: 'firma' })
    expect(localStorage.getItem('ferrebot_token')).toBe('jwt-xyz')
    expect(screen.getByText('HOME OK')).toBeInTheDocument()   // navegó a /
  })

  it('en 403 muestra el mensaje de "pídele a Andrés"', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))

    renderLogin()
    await act(async () => { await window.onTelegramAuth(PAYLOAD) })

    expect(screen.getByText(/Pídele a Andrés/)).toBeInTheDocument()
    expect(localStorage.getItem('ferrebot_token')).toBeNull()
  })
})
