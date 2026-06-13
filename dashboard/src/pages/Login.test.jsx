import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
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
  // El interceptor de 401 de api() redirige vía redirector → en jsdom rompería: lo neutralizamos.
  // (En jsdom el host es localhost → sin landing → cae a toLogin.)
  vi.spyOn(redirector, 'toLogin').mockImplementation(() => {})
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
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


describe('Login — Telegram retirado (login único en la landing)', () => {
  it('NO monta el widget de Telegram aunque exista VITE_TELEGRAM_BOT_USERNAME', () => {
    vi.stubEnv('VITE_TELEGRAM_BOT_USERNAME', 'elmicha_bot')
    renderLogin()

    // Ni hook global, ni script del widget, ni el divisor "o" de la entrada alterna.
    expect(window.onTelegramAuth).toBeUndefined()
    expect(document.querySelector('script[data-telegram-login]')).toBeNull()
    expect(screen.queryByText('o')).toBeNull()
    // El formulario email+contraseña sigue ahí.
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Contraseña')).toBeInTheDocument()
  })
})
