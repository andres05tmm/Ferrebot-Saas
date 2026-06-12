import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Outlet } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { isRouteEnabled } from '@/lib/features.jsx'
import TabConversaciones, { haceCuanto, ventanaAbierta } from './TabConversaciones.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const ahora = () => new Date().toISOString()
const haceHoras = (h) => new Date(Date.now() - h * 3600_000).toISOString()

const CONVS = [
  {
    id: 1, cliente_telefono: '573001112233', estado: 'humano', motivo: 'Pide asesor',
    creada_en: haceHoras(2), escalada_en: haceHoras(1), resuelta_en: null,
    ultimo_texto: 'quiero un asesor', ultimo_autor: 'cliente', ultimo_en: haceHoras(1),
  },
  {
    id: 2, cliente_telefono: '573004445566', estado: 'bot', motivo: null,
    creada_en: haceHoras(3), escalada_en: null, resuelta_en: null,
    ultimo_texto: '¡gracias!', ultimo_autor: 'cliente', ultimo_en: haceHoras(3),
  },
]
// Conv 1 (humano): último entrante reciente → ventana de 24h ABIERTA.
const MSGS_1 = [
  { id: 10, cliente_telefono: '573001112233', direccion: 'entrante', autor: 'cliente', texto: 'Hola, necesito ayuda', creada_en: haceHoras(1) },
  { id: 11, cliente_telefono: '573001112233', direccion: 'saliente', autor: 'bot', texto: 'Te conecto con un asesor', creada_en: haceHoras(1) },
]
const MSGS_2 = [
  { id: 20, cliente_telefono: '573004445566', direccion: 'entrante', autor: 'cliente', texto: '¿a qué hora abren?', creada_en: haceHoras(3) },
  { id: 21, cliente_telefono: '573004445566', direccion: 'saliente', autor: 'bot', texto: 'Abrimos 8am', creada_en: haceHoras(3) },
]

function instalarFetch({ convs = CONVS, msgs1 = MSGS_1 } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    const m = opts.method || 'GET'
    calls.push([u, m, opts.body])
    if (/\/conversaciones\/\d+\/(responder|tomar|resolver)/.test(u)) {
      return Promise.resolve(jsonResp({ id: 99, estado: 'humano', autor: 'asesor', direccion: 'saliente', texto: 'ok' }))
    }
    if (/\/conversaciones\/1\/mensajes/.test(u)) return Promise.resolve(jsonResp(msgs1))
    if (/\/conversaciones\/2\/mensajes/.test(u)) return Promise.resolve(jsonResp(MSGS_2))
    if (u.endsWith('/conversaciones')) return Promise.resolve(jsonResp(convs))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

// Harness con Outlet (refreshKey), como el shell real.
function Harness() {
  const [k, setK] = useState(0)
  return (
    <MemoryRouter>
      <button onClick={() => setK(x => x + 1)}>refrescar</button>
      <Routes>
        <Route element={<Outlet context={{ refreshKey: k }} />}>
          <Route index element={<TabConversaciones />} />
        </Route>
      </Routes>
    </MemoryRouter>
  )
}

const getsInbox = (calls) =>
  calls.filter(([u, m]) => u.endsWith('/conversaciones') && m === 'GET').length

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabConversaciones — gating de ruta', () => {
  it('/conversaciones se oculta sin canal_whatsapp y se ve con la feature', () => {
    expect(isRouteEnabled('/conversaciones', [])).toBe(false)
    expect(isRouteEnabled('/conversaciones', ['pack_agenda'])).toBe(false)
    expect(isRouteEnabled('/conversaciones', ['canal_whatsapp'])).toBe(true)
  })
})

describe('TabConversaciones — inbox', () => {
  it('lista las conversaciones (teléfono + último mensaje + estado)', async () => {
    instalarFetch()
    render(<Harness />)
    expect(await screen.findByText('573001112233')).toBeInTheDocument()
    expect(screen.getByText('573004445566')).toBeInTheDocument()
    expect(screen.getByText('quiero un asesor')).toBeInTheDocument()
    expect(screen.getAllByText('Necesita humano').length).toBeGreaterThan(0)
  })

  it('al seleccionar una conversación carga y pinta su hilo', async () => {
    instalarFetch()
    render(<Harness />)
    fireEvent.click(await screen.findByText('573001112233'))
    expect(await screen.findByText('Hola, necesito ayuda')).toBeInTheDocument()
    expect(screen.getByText('Te conecto con un asesor')).toBeInTheDocument()
  })

  it('conversación en humano + ventana abierta: responder hace POST /responder', async () => {
    const { calls } = instalarFetch()
    render(<Harness />)
    fireEvent.click(await screen.findByText('573001112233'))
    await screen.findByText('Hola, necesito ayuda')

    const input = screen.getByLabelText('Mensaje para el cliente')
    expect(input).not.toBeDisabled()
    fireEvent.change(input, { target: { value: 'Te atiendo yo' } })
    fireEvent.click(screen.getByLabelText('Enviar mensaje'))

    await waitFor(() => {
      const call = calls.find(([u, m]) => /\/conversaciones\/1\/responder/.test(u) && m === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[2])).toEqual({ texto: 'Te atiendo yo' })
    })
  })

  it('conversación con el bot: el composer está deshabilitado y aparece "Tomar conversación"', async () => {
    const { calls } = instalarFetch()
    render(<Harness />)
    fireEvent.click(await screen.findByText('573004445566'))
    await screen.findByText('¿a qué hora abren?')

    expect(screen.getByLabelText('Mensaje para el cliente')).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Tomar conversación'))
    await waitFor(() => {
      expect(calls.some(([u, m]) => /\/conversaciones\/2\/tomar/.test(u) && m === 'POST')).toBe(true)
    })
  })

  it('"Devolver al bot" hace POST /resolver', async () => {
    const { calls } = instalarFetch()
    render(<Harness />)
    fireEvent.click(await screen.findByText('573001112233'))
    await screen.findByText('Hola, necesito ayuda')
    fireEvent.click(screen.getByLabelText('Devolver al bot'))
    await waitFor(() => {
      expect(calls.some(([u, m]) => /\/conversaciones\/1\/resolver/.test(u) && m === 'POST')).toBe(true)
    })
  })

  it('fuera de la ventana de 24h: el composer se deshabilita aunque esté en humano', async () => {
    const viejos = [
      { id: 10, cliente_telefono: '573001112233', direccion: 'entrante', autor: 'cliente', texto: 'mensaje viejo', creada_en: haceHoras(30) },
    ]
    instalarFetch({ msgs1: viejos })
    render(<Harness />)
    fireEvent.click(await screen.findByText('573001112233'))
    await screen.findByText('mensaje viejo')
    expect(screen.getByLabelText('Mensaje para el cliente')).toBeDisabled()
    expect(screen.getByText(/ventana de 24h/i)).toBeInTheDocument()
  })

  it('filtro "En humano" oculta las conversaciones con el bot', async () => {
    instalarFetch()
    render(<Harness />)
    await screen.findByText('573001112233')
    fireEvent.click(screen.getByText('En humano'))
    expect(screen.queryByText('573004445566')).toBeNull()      // la del bot se fue
    expect(screen.getByText('573001112233')).toBeInTheDocument()
  })

  it('tiempo real: un evento conversacion_mensaje refetchea la lista', async () => {
    const { calls } = instalarFetch()
    render(<Harness />)
    await screen.findByText('573001112233')
    const antes = getsInbox(calls)
    act(() => { rtHandler?.('conversacion_mensaje', { cliente_telefono: '573001112233' }) })
    await waitFor(() => expect(getsInbox(calls)).toBeGreaterThan(antes))
  })

  it('el botón refrescar del shell (refreshKey) re-fetchea la lista', async () => {
    const { calls } = instalarFetch()
    render(<Harness />)
    await screen.findByText('573001112233')
    const antes = getsInbox(calls)
    fireEvent.click(screen.getByText('refrescar'))
    await waitFor(() => expect(getsInbox(calls)).toBeGreaterThan(antes))
  })
})

describe('haceCuanto', () => {
  it('formatea minutos/horas/días en relativo', () => {
    const base = new Date('2026-06-07T12:00:00-05:00').getTime()
    expect(haceCuanto('2026-06-07T11:58:00-05:00', base)).toBe('hace 2 min')
    expect(haceCuanto('2026-06-07T09:00:00-05:00', base)).toBe('hace 3 h')
    expect(haceCuanto('2026-06-05T12:00:00-05:00', base)).toBe('hace 2 días')
    expect(haceCuanto(null)).toBe('—')
  })
})

describe('ventanaAbierta', () => {
  const base = Date.now()
  it('abierta si el último entrante fue hace < 24h', () => {
    expect(ventanaAbierta([{ direccion: 'entrante', creada_en: haceHoras(5) }], base)).toBe(true)
  })
  it('cerrada si el último entrante fue hace > 24h', () => {
    expect(ventanaAbierta([{ direccion: 'entrante', creada_en: haceHoras(30) }], base)).toBe(false)
  })
  it('cerrada si no hay mensajes entrantes', () => {
    expect(ventanaAbierta([{ direccion: 'saliente', creada_en: haceHoras(1) }], base)).toBe(false)
    expect(ventanaAbierta([], base)).toBe(false)
  })
})
