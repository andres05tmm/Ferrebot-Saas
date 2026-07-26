import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabProveedores from './TabProveedores.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const FERRE = {
  id: 1, nombre: 'Ferre Mayorista', nit: '900123', telefono: null, contacto_nombre: null,
  contacto_telefono: null, saldo_pendiente: '150000.00', vencido: '50000.00',
  facturas_pendientes: 2, pedidos_en_camino: 1, lead_time_promedio_horas: 48, ultima_compra: '2026-07-01',
}
const TORNI = {
  ...FERRE, id: 2, nombre: 'Tornillos SA', nit: null, saldo_pendiente: '0.00', vencido: '0.00',
  facturas_pendientes: 0, pedidos_en_camino: 0, lead_time_promedio_horas: null,
}
const CUENTA = {
  proveedor_id: 1, proveedor_nombre: 'Ferre Mayorista', desde: '2026-01-26', hasta: '2026-07-26',
  saldo_anterior: '0.00', saldo_pendiente: '150000.00', vencido: '50000.00',
  aging: { '0-30': '100000.00', '31-60': '50000.00', '61-90': '0.00', '90+': '0.00' },
  movimientos: [
    { fecha: '2026-06-05', tipo: 'factura', referencia: 'FAC-9', cargo: '200000.00', abono: '0.00', saldo: '200000.00', medio: null },
    { fecha: '2026-06-20', tipo: 'abono', referencia: 'FAC-9', cargo: '0.00', abono: '50000.00', saldo: '150000.00', medio: 'caja' },
  ],
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><TabProveedores /></MemoryRouter>
    </QueryClientProvider>,
  )
}

// Backend por defecto: 2 proveedores, la cuenta de Ferre y listas vacías para el resto.
function mockApi(extra) {
  return vi.fn((url, opts) => {
    const u = String(url)
    const custom = extra?.(u, opts)
    if (custom) return custom
    if (u.includes('/proveedores/estado')) return Promise.resolve(jsonResp([FERRE, TORNI]))
    if (u.includes('/estado-cuenta')) return Promise.resolve(jsonResp(CUENTA))
    if (u.includes('/proveedores/facturas')) return Promise.resolve(jsonResp([]))
    if (u.includes('/pagar/cuentas')) return Promise.resolve(jsonResp([]))
    return Promise.resolve(jsonResp([]))
  })
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabProveedores', () => {
  it('lista todos los proveedores con lo que se le debe a cada uno y los totales arriba', async () => {
    vi.stubGlobal('fetch', mockApi())
    renderTab()

    // El nombre sale dos veces (lista + cabecera de la ficha del seleccionado).
    expect((await screen.findAllByText('Ferre Mayorista')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /Tornillos SA/ })).toBeInTheDocument()
    // Total adeudado = suma de los saldos; vencido = suma de los vencidos.
    expect(screen.getAllByText('$150.000').length).toBeGreaterThan(0)
    expect(screen.getAllByText('$50.000').length).toBeGreaterThan(0)
    expect(screen.getByText('Proveedores con deuda').parentElement.parentElement)
      .toHaveTextContent('1')
  })

  it('el estado de cuenta del seleccionado trae facturas y abonos con saldo corrido', async () => {
    vi.stubGlobal('fetch', mockApi())
    renderTab()

    expect(await screen.findByText('Saldo anterior')).toBeInTheDocument()
    const fila = (await screen.findByText('Factura', { exact: false, selector: 'td' })).closest('tr')
    // La factura carga 200.000 y deja el saldo corrido en 200.000 (debe + saldo en la misma fila).
    expect(within(fila).getAllByText('$200.000')).toHaveLength(2)
    expect(screen.getByText(/efectivo de caja/)).toBeInTheDocument()   // el medio del abono
  })

  it('el buscador filtra la lista y al elegir otro proveedor pide SU estado de cuenta', async () => {
    const fetchMock = mockApi()
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.change(await screen.findByLabelText('Buscar proveedor'), { target: { value: 'torni' } })
    expect(screen.queryByRole('button', { name: /Ferre Mayorista/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Tornillos SA/ }))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/proveedores/2/estado-cuenta'))).toBe(true)
    })
  })

  it('registrar factura desde la ficha la ata al proveedor (proveedor_id en el POST)', async () => {
    const fetchMock = mockApi((u, opts) => {
      if (u.includes('/proveedores/facturas') && opts?.method === 'POST') {
        return Promise.resolve(jsonResp({ id: 'FAC-10' }, 201))
      }
      return null
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Registrar factura' }))
    fireEvent.change(await screen.findByLabelText('Número de factura'), { target: { value: 'FAC-10' } })
    fireEvent.change(screen.getByLabelText('Total'), { target: { value: '100000' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar factura/ }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/facturas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        id: 'FAC-10', proveedor: 'Ferre Mayorista', proveedor_id: 1, descripcion: null, total: 100000,
      })
    })
  })

  it('el abono va por el modal COMPARTIDO y postea con el origen del dinero', async () => {
    const fetchMock = mockApi((u, opts) => {
      if (u.includes('/proveedores/abonos') && opts?.method === 'POST') {
        return Promise.resolve(jsonResp({ id: 'A' }, 201))
      }
      if (u.includes('/proveedores/facturas')) {
        return Promise.resolve(jsonResp([{
          id: 'A', proveedor: 'Ferre Mayorista', proveedor_id: 1, total: '100000.00',
          pagado: '0.00', pendiente: '100000.00', estado: 'pendiente', foto_url: null,
        }]))
      }
      return null
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Abonar' }))
    fireEvent.change(await screen.findByLabelText('Factura'), { target: { value: 'A' } })
    fireEvent.change(screen.getByLabelText('Monto del abono'), { target: { value: '30000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Registrar abono' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/abonos') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ factura_id: 'A', monto: 30000, origen_fondos: 'caja' })
    })
  })

  it('las facturas sin proveedor se muestran aparte para asignarlas a mano', async () => {
    const fetchMock = mockApi((u, opts) => {
      if (u.includes('/proveedor') && opts?.method === 'PUT') return Promise.resolve(jsonResp({}, 200))
      if (u.includes('/proveedores/facturas')) {
        return Promise.resolve(jsonResp([{
          id: 'VIEJA-1', proveedor: 'ferremayorista', proveedor_id: null, total: '80000.00',
          pagado: '0.00', pendiente: '80000.00', estado: 'pendiente', foto_url: null,
        }]))
      }
      return null
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.change(await screen.findByLabelText('Proveedor de VIEJA-1'), { target: { value: '1' } })
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/facturas/VIEJA-1/proveedor'))
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ proveedor_id: 1 })
    })
  })

  it('"Nuevo" da de alta un proveedor con sus datos de contacto', async () => {
    const fetchMock = mockApi((u, opts) => {
      if (u.endsWith('/proveedores') && opts?.method === 'POST') {
        return Promise.resolve(jsonResp({ id: 3, nombre: 'Distribuidora X' }, 201))
      }
      return null
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Nuevo/ }))
    fireEvent.change(await screen.findByLabelText('Nombre del proveedor'), { target: { value: 'Distribuidora X' } })
    fireEvent.change(screen.getByLabelText('Teléfono (opcional)'), { target: { value: '3001234567' } })
    fireEvent.click(screen.getByRole('button', { name: 'Registrar proveedor' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).endsWith('/proveedores') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        nombre: 'Distribuidora X', nit: null, telefono: '3001234567', correo: null,
        contacto_nombre: null, contacto_telefono: null,
      })
    })
  })

  it('"Editar" en la ficha corrige los datos del proveedor (PUT)', async () => {
    const fetchMock = mockApi((u, opts) => {
      if (u.includes('/proveedores/1') && opts?.method === 'PUT') {
        return Promise.resolve(jsonResp({ id: 1, nombre: 'Ferre Mayorista' }, 200))
      }
      return null
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    fireEvent.change(await screen.findByLabelText('Teléfono (opcional)'), { target: { value: '3009999999' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar cambios' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/proveedores/1') && c[1]?.method === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body).telefono).toBe('3009999999')
      expect(JSON.parse(call[1].body).nombre).toBe('Ferre Mayorista')   // prellenado, no se pierde
    })
  })

  it('vendedor: sin acceso al tab', async () => {
    authState.admin = false
    const fetchMock = mockApi()
    vi.stubGlobal('fetch', fetchMock)
    renderTab()

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/proveedores'))).toBe(false)
  })
})
