/* SetPassword (A1.5): toma el token de la URL y pega a /auth/set-password; maneja token inválido. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SetPassword from './SetPassword.jsx'

function jsonResp(data, status = 200) {
  return { ok: status < 400, status, json: async () => data }
}

function renderEn(ruta) {
  return render(<MemoryRouter initialEntries={[ruta]}><SetPassword /></MemoryRouter>)
}

function _establecer(password = 'clave-larga-1', confirm = 'clave-larga-1') {
  fireEvent.change(screen.getByLabelText('Nueva contraseña'), { target: { value: password } })
  fireEvent.change(screen.getByLabelText('Confirmar contraseña'), { target: { value: confirm } })
  fireEvent.click(screen.getByRole('button', { name: 'Guardar contraseña' }))
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('SetPassword', () => {
  it('con token válido pega a /auth/set-password con {token,password} y confirma éxito', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp({ detail: 'ok' })))
    vi.stubGlobal('fetch', fetchMock)
    renderEn('/set-password?token=tok-abc')
    _establecer()

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/auth/set-password'))
      expect(call).toBeTruthy()
      expect(call[1].method).toBe('POST')
      expect(JSON.parse(call[1].body)).toEqual({ token: 'tok-abc', password: 'clave-larga-1' })
    })
    expect(await screen.findByText(/tu contraseña quedó establecida/i)).toBeInTheDocument()
  })

  it('token inválido/expirado (400) muestra mensaje claro', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp({ detail: 'Token inválido' }, 400))))
    renderEn('/set-password?token=viejo')
    _establecer()
    expect(await screen.findByText(/no es válido o ya expiró/i)).toBeInTheDocument()
  })

  it('contraseñas que no coinciden no llaman al endpoint', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp({})))
    vi.stubGlobal('fetch', fetchMock)
    renderEn('/set-password?token=tok')
    _establecer('clave-larga-1', 'distinta-9')
    expect(await screen.findByText(/no coinciden/i)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('sin token en la URL avisa que el enlace es inválido', () => {
    renderEn('/set-password')
    expect(screen.getByText(/falta el token/i)).toBeInTheDocument()
  })
})
