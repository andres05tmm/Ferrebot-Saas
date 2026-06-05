import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import { isRouteEnabled } from '@/lib/features.jsx'
import TabComprasFiscal from './TabComprasFiscal.jsx'

const FISCALES = [
  // Sin CUFE → muestra el campo CUFE + botón Importar.
  { id: 1, compra_id: null, proveedor_nit: '900111', base: '84033.61', iva: '15966.39', total: '100000.00', soporte_url: null, creado_en: '2026-06-05T12:00:00-05:00', cufe_proveedor: null, evento_estado: null },
  // Con CUFE ya importado → muestra Aceptar / Reclamar + estado de eventos.
  { id: 2, compra_id: 8, proveedor_nit: '901', base: '0.00', iva: '0.00', total: '5000.00', soporte_url: null, creado_en: '2026-06-05T12:00:00-05:00', cufe_proveedor: 'CUFEABCDEF1234567890', evento_030_at: '2026-06-05T12:00:00-05:00', evento_estado: 'pendiente' },
]
const COMPRAS = [
  { id: 7, proveedor_id: 1, proveedor_nombre: 'Ferre Mayorista', fecha: '2026-06-05T12:00:00+00:00', total: '80000.00' },
  { id: 8, proveedor_id: 2, proveedor_nombre: 'Otra Ferre', fecha: '2026-06-05T12:00:00+00:00', total: '5000.00' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    const m = opts?.method
    // Orden importa: rutas más específicas primero ('/compras-fiscal' es prefijo de varias).
    if (u.includes('/compras-fiscal/ambiente')) return Promise.resolve(jsonResp({ ambiente: 'pruebas' }))
    if (/\/compras-fiscal\/\d+\/(importar|aceptar|reclamar)/.test(u) && m === 'POST') {
      return Promise.resolve(jsonResp({ id: 1, evento_estado: 'pendiente', evento_030_at: 'x' }, 200))
    }
    if (u.includes('to-fiscal') && m === 'POST') return Promise.resolve(jsonResp({ id: 9, compra_id: 7, base: '0.00', iva: '0.00', total: '80000.00' }, 201))
    if (u.includes('/compras-fiscal') && m === 'POST') return Promise.resolve(jsonResp({ id: 5, total: '119.00' }, 201))
    if (u.includes('/compras-fiscal')) return Promise.resolve(jsonResp(FISCALES))
    if (u.includes('/compras')) return Promise.resolve(jsonResp(COMPRAS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const tienePost = (fetchMock, re) =>
  fetchMock.mock.calls.some(c => re.test(String(c[0])) && c[1]?.method === 'POST')

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabComprasFiscal — datos (6a)', () => {
  it('admin: registra una compra fiscal (POST /compras-fiscal con el shape correcto) y ve la lista', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)

    expect(await screen.findByText('NIT 900111')).toBeInTheDocument()   // lista del rango

    fireEvent.change(screen.getByLabelText('NIT del proveedor'), { target: { value: '900111' } })
    fireEvent.change(screen.getByLabelText('Base'), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('IVA'), { target: { value: '19' } })
    fireEvent.change(screen.getByLabelText('Total'), { target: { value: '119' } })
    fireEvent.click(screen.getByText('Registrar compra fiscal'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => /\/compras-fiscal$|\/compras-fiscal\?/.test(String(c[0])) && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        proveedor_nit: '900111', base: 100, iva: 19, total: 119, soporte_url: null,
      })
    })
  })

  it('admin: "marcar fiscal" sobre una compra normal postea POST /compras/{id}/to-fiscal', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)

    expect(await screen.findByText('Ferre Mayorista')).toBeInTheDocument()
    fireEvent.click(screen.getByText('marcar fiscal'))

    await waitFor(() => expect(tienePost(fetchMock, /\/compras\/7\/to-fiscal/)).toBe(true))
  })

  it('vendedor: no ve los controles de registro', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(screen.queryByText('Registrar compra fiscal')).toBeNull()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/compras-fiscal'))).toBe(false)
  })

  it('el tab está gateado: la ruta no aparece sin la feature', () => {
    expect(isRouteEnabled('/compras-fiscal', [])).toBe(false)
    expect(isRouteEnabled('/compras-fiscal', ['compras_fiscal'])).toBe(true)
  })
})

describe('TabComprasFiscal — RADIAN FE recibidas (6b)', () => {
  it('importar: abre confirmación fuerte (DIAN REAL + ambiente) y SOLO al confirmar postea /importar', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)
    await screen.findByText('NIT 900111')

    // Pega el CUFE y abre la confirmación (todavía NO postea).
    fireEvent.change(screen.getByLabelText('CUFE 1'), { target: { value: 'CUFE-PEGADO-123' } })
    fireEvent.click(screen.getByText('Importar'))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/DIAN REAL/)).toBeInTheDocument()
    expect(within(dialog).getByText('pruebas')).toBeInTheDocument()   // ambiente en la confirmación
    expect(tienePost(fetchMock, /\/compras-fiscal\/1\/importar/)).toBe(false)

    // Cancelar NO postea.
    fireEvent.click(within(dialog).getByText('Cancelar'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(tienePost(fetchMock, /\/compras-fiscal\/1\/importar/)).toBe(false)

    // Confirmar SÍ postea, con el CUFE en el body.
    fireEvent.click(screen.getByText('Importar'))
    fireEvent.click(within(await screen.findByRole('dialog')).getByText('Sí, enviar evento'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => /\/compras-fiscal\/1\/importar/.test(String(c[0])) && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ cufe: 'CUFE-PEGADO-123' })
    })
  })

  it('aceptar: sobre una FE con CUFE importado, confirma y postea /aceptar', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)
    await screen.findByText('NIT 901')

    fireEvent.click(screen.getByText('Aceptar'))
    fireEvent.click(within(await screen.findByRole('dialog')).getByText('Sí, enviar evento'))

    await waitFor(() => expect(tienePost(fetchMock, /\/compras-fiscal\/2\/aceptar/)).toBe(true))
  })

  it('pinta el estado de los eventos de la FE recibida', async () => {
    instalarFetch()
    render(<MemoryRouter><TabComprasFiscal /></MemoryRouter>)
    expect(await screen.findByText('pendiente')).toBeInTheDocument()   // badge de evento_estado
  })
})
