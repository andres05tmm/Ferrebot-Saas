import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabProveedores from './TabProveedores.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }
const RESUMEN = { total_adeudado: '0.00', facturas_pendientes: 0 }

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabProveedores', () => {
  it('registrar factura postea el shape correcto (POST /proveedores/facturas)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/proveedores/facturas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 'FAC-9' }, 201))
      if (u.includes('/proveedores/resumen')) return Promise.resolve(jsonResp(RESUMEN))
      if (u.includes('/proveedores/facturas')) return Promise.resolve(jsonResp([]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabProveedores /></MemoryRouter>)

    fireEvent.change(await screen.findByLabelText('Número de factura'), { target: { value: 'FAC-9' } })
    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Ferre Mayorista' } })
    fireEvent.change(screen.getByLabelText('Total'), { target: { value: '100000' } })
    fireEvent.click(screen.getByText('Registrar factura'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/facturas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ id: 'FAC-9', proveedor: 'Ferre Mayorista', descripcion: null, total: 100000 })
    })
  })

  it('registrar factura con vencimiento incluye fecha_vencimiento en el POST y lo muestra en la lista', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/proveedores/facturas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 'FAC-9' }, 201))
      if (u.includes('/proveedores/resumen')) return Promise.resolve(jsonResp(RESUMEN))
      if (u.includes('/proveedores/facturas')) return Promise.resolve(jsonResp([
        { id: 'FAC-9', proveedor: 'Ferre', total: '100000.00', pagado: '0.00', pendiente: '100000.00', estado: 'pendiente', fecha: '2026-06-05', fecha_vencimiento: '2026-07-05', foto_url: null },
      ]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabProveedores /></MemoryRouter>)

    fireEvent.change(await screen.findByLabelText('Número de factura'), { target: { value: 'FAC-9' } })
    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Ferre' } })
    fireEvent.change(screen.getByLabelText('Total'), { target: { value: '100000' } })
    fireEvent.change(screen.getByLabelText('Fecha factura'), { target: { value: '2026-06-05' } })
    fireEvent.change(screen.getByLabelText('Fecha de vencimiento'), { target: { value: '2026-07-05' } })
    fireEvent.click(screen.getByText('Registrar factura'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/facturas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        id: 'FAC-9', proveedor: 'Ferre', descripcion: null, total: 100000,
        fecha: '2026-06-05', fecha_vencimiento: '2026-07-05',
      })
    })
    expect(await screen.findByText(/vence 2026-07-05/)).toBeTruthy()
  })

  it('registrar abono vía el modal COMPARTIDO postea el shape y el saldo se actualiza', async () => {
    let pendiente = '100000.00'
    const factura = () => ({ id: 'A', proveedor: 'X', total: '100000.00', pagado: '0.00', pendiente, estado: 'pendiente', foto_url: null })
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/proveedores/abonos') && opts?.method === 'POST') {
        pendiente = '70000.00'   // el backend recalcula; el refetch lo refleja
        return Promise.resolve(jsonResp({ ...factura(), pagado: '30000.00' }, 201))
      }
      if (u.includes('/proveedores/resumen')) return Promise.resolve(jsonResp(RESUMEN))
      if (u.includes('/proveedores/facturas')) return Promise.resolve(jsonResp([factura()]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabProveedores /></MemoryRouter>)

    expect(await screen.findByText('$100.000')).toBeInTheDocument()   // pendiente inicial

    // El abono va por ModalAbonoProveedor (F4): el MISMO modal del cockpit /hoy.
    fireEvent.click(screen.getByRole('button', { name: /Nuevo abono/ }))
    fireEvent.change(await screen.findByLabelText('Factura'), { target: { value: 'A' } })
    fireEvent.change(screen.getByLabelText('Monto del abono'), { target: { value: '30000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Registrar abono' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/abonos') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ factura_id: 'A', monto: 30000 })
    })
    expect(await screen.findByText('$70.000')).toBeInTheDocument()    // saldo recalculado tras el refetch
  })

  it('el control de foto se oculta con aviso si el endpoint da 503', async () => {
    const factura = { id: 'A', proveedor: 'X', total: '100000.00', pagado: '0.00', pendiente: '100000.00', estado: 'pendiente', foto_url: null }
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/foto') && opts?.method === 'POST') return Promise.resolve(jsonResp({ detail: 'no' }, 503))
      if (u.includes('/proveedores/resumen')) return Promise.resolve(jsonResp(RESUMEN))
      if (u.includes('/proveedores/facturas')) return Promise.resolve(jsonResp([factura]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabProveedores /></MemoryRouter>)

    const input = await screen.findByLabelText('Subir foto A')   // control visible (optimista)
    const file = new File(['datos'], 'soporte.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    expect(await screen.findByText(/fotos de soporte están deshabilitadas/i)).toBeInTheDocument()
    expect(screen.queryByLabelText('Subir foto A')).toBeNull()   // el control desaparece
  })

  it('vendedor: sin acceso a cuentas por pagar', async () => {
    authState.admin = false
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp([])))
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><TabProveedores /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/proveedores'))).toBe(false)
  })
})
