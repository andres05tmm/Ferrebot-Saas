import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import Login from './Login.jsx'
import { APP_URL, MENSAJES } from '@/lib/auth.js'

// El shader es WebGL puro (no aplica en jsdom).
vi.mock('@/components/AuroraOro.jsx', () => ({ default: () => null }))

const respuesta = (status, body = {}) =>
  Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) })

function montar(ruta = '/login') {
  render(
    <MemoryRouter initialEntries={[ruta]}>
      <Login />
    </MemoryRouter>,
  )
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

  async function destinoTrasLogin(body, ruta) {
    vi.stubGlobal('fetch', vi.fn(() => respuesta(200, body)))
    montar(ruta)
    await llenarYEnviar(userEvent.setup())
    await waitFor(() => expect(window.location.assign).toHaveBeenCalledTimes(1))
    return window.location.assign.mock.calls[0][0]
  }

  it('al éxito redirige al dashboard con el token en el fragmento', async () => {
    const destino = await destinoTrasLogin({ token: 'jwt-123', usuario: { rol: 'admin', tenant: 'brasa' } })
    expect(destino).toContain('#token=jwt-123')
  })

  it('aislamiento: ?next de OTRA empresa se rechaza en seco (no navega, avisa)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respuesta(200, { token: 'jwt-123', usuario: { rol: 'admin', tenant: 'brasa' } })))
    montar('/login?next=barberia-demo')
    await llenarYEnviar(userEvent.setup())
    expect(await screen.findByRole('alert')).toHaveTextContent(MENSAJES.otraEmpresa)
    expect(window.location.assign).not.toHaveBeenCalled()
  })

  it('?next que COINCIDE con el tenant del JWT enruta a su subdominio', async () => {
    const destino = await destinoTrasLogin(
      { token: 'jwt-123', usuario: { rol: 'admin', tenant: 'brasa' } },
      '/login?next=brasa',
    )
    expect(destino).toBe('https://brasa.melquiadez.com/#token=jwt-123')
  })

  it('sin ?next (entrada neutra melquiadez.com/login) entra al tenant del JWT', async () => {
    const destino = await destinoTrasLogin({ token: 'jwt-123', usuario: { rol: 'admin', tenant: 'brasa' } })
    expect(destino).toBe('https://brasa.melquiadez.com/#token=jwt-123')
  })

  it('super_admin (tenant null) cae a app. (plataforma)', async () => {
    const destino = await destinoTrasLogin({ token: 'jwt-123', usuario: { rol: 'super_admin', tenant: null } })
    expect(destino).toBe(`${APP_URL}/#token=jwt-123`)
  })

  it('super_admin no se bloquea aunque haya ?next (opera cross-tenant → app.)', async () => {
    const destino = await destinoTrasLogin(
      { token: 'jwt-123', usuario: { rol: 'super_admin', tenant: null } },
      '/login?next=barberia-demo',
    )
    expect(destino).toBe(`${APP_URL}/#token=jwt-123`)
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
