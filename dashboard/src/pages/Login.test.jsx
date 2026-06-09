import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Login from './Login.jsx'
import { redirector } from '@/lib/api.js'

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>HOME OK</div>} />
        <Route path="/recuperar" element={<div>RECUPERAR</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

function jsonResp(data, status = 200) {
  return { ok: status < 400, status, json: async () => data }
}

beforeEach(() => {
  localStorage.clear()
  vi.stubEnv('VITE_TELEGRAM_BOT_USERNAME', 'elmicha_bot')
  // El interceptor de 401 de api() redirige vía redirector → en jsdom rompería: lo neutralizamos.
  vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
  delete window.onTelegramAuth
})


describe('Login email/contraseña (entrada primaria, A1.5)', () => {
  function _llenar(email = 'ana@clinica.co', password = 'clave-correcta') {
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } })
    fireEvent.change(screen.getByLabelText('Contraseña'), { target: { value: password } })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar' }))
  }

  it('login correcto: pega a /auth/login/password, guarda el token y navega al shell', async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResp({ token: 'jwt-123', usuario: { id: 42, rol: 'admin', tenant: 'clinica' } })),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderLogin()
    _llenar()

    expect(await screen.findByText('HOME OK')).toBeInTheDocument()          // navegó a /
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/auth/login/password'))
    expect(call).toBeTruthy()
    expect(call[1].method).toBe('POST')
    expect(JSON.parse(call[1].body)).toEqual({ email: 'ana@clinica.co', password: 'clave-correcta' })
    expect(localStorage.getItem('ferrebot_token')).toBe('jwt-123')
  })

  it('401: muestra error de credenciales y NO navega', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp({ detail: 'x' }, 401))))
    renderLogin()
    _llenar('ana@clinica.co', 'mala')

    expect(await screen.findByText('Email o contraseña incorrectos.')).toBeInTheDocument()
    expect(screen.queryByText('HOME OK')).toBeNull()
  })

  it('429: muestra aviso de bloqueo por intentos', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp({ detail: 'x' }, 429))))
    renderLogin()
    _llenar()

    expect(await screen.findByText(/Demasiados intentos/)).toBeInTheDocument()
    expect(screen.queryByText('HOME OK')).toBeNull()
  })
})


const PAYLOAD = { id: 7, first_name: 'Ana', username: 'ana', auth_date: 1700000000, hash: 'firma' }

describe('Login (Telegram Login Widget — entrada alterna)', () => {
  it('onTelegramAuth postea a /api/v1/auth/login y en 200 guarda y navega', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResp({ token: 'jwt-xyz', usuario: { id: 7, rol: 'admin', tenant: 'pr' } }),
    )
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
