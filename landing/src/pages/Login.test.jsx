import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Login from './Login.jsx'
import { MENSAJES } from '@/lib/auth.js'

// El shader es WebGL puro (no aplica en jsdom).
vi.mock('@/components/AuroraOro.jsx', () => ({ default: () => null }))

const respuesta = (status, body = {}) =>
  Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) })

function montar() {
  render(<MemoryRouter><Login /></MemoryRouter>)
}

async function llenarYEnviar(user) {
  await user.type(screen.getByLabelText('Email'), 'ana@negocio.com')
  await user.type(screen.getByLabelText('Contraseña'), 'clave123')
  await user.click(screen.getByRole('button', { name: /entrar/i }))
}

describe('/login', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    // jsdom no permite navegar: se intercepta el assign del handoff
    Object.defineProperty(window, 'location', {
      value: { ...window.location, assign: vi.fn() },
      writable: true,
    })
  })

  it('al éxito redirige al dashboard con el token en el fragmento', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respuesta(200, { token: 'jwt-123', usuario: { rol: 'admin' } })))
    montar()
    await llenarYEnviar(userEvent.setup())
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledTimes(1))
    const destino = window.location.assign.mock.calls[0][0]
    expect(destino).toContain('#token=jwt-123')
  })

  it('401 muestra el mensaje genérico y no navega', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respuesta(401)))
    montar()
    await llenarYEnviar(userEvent.setup())
    expect(await screen.findByRole('alert')).toHaveTextContent(MENSAJES.credenciales)
    expect(window.location.assign).not.toHaveBeenCalled()
  })

  it('429 avisa del bloqueo temporal', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respuesta(429)))
    montar()
    await llenarYEnviar(userEvent.setup())
    expect(await screen.findByRole('alert')).toHaveTextContent(MENSAJES.bloqueado)
  })

  it('error de red muestra mensaje de conexión', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('failed'))))
    montar()
    await llenarYEnviar(userEvent.setup())
    expect(await screen.findByRole('alert')).toHaveTextContent(MENSAJES.conexion)
  })
})
