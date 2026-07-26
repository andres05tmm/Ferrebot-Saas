/*
 * Ciclo de la compra en el tab Compras: registrar el pedido (con productos, cantidad y costo
 * unitario + forma de pago), marcar que llegó y corregir después si se digitó mal.
 * Absorbe los casos del viejo TabPedidosProveedor.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabCompras from '../TabCompras.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'
import { conQuery } from '@/test/query.jsx'

const CON_CICLO = ['pos', 'inventario', 'pedidos_proveedor']

const EN_CAMINO = {
  id: 7, proveedor_id: 1, proveedor_nombre: 'Ferrisariato',
  fecha_pedido: '2026-07-08T14:00:00-05:00', fecha_estimada: null, estado: 'pedido',
  descripcion: 'Pedido del lunes', monto_estimado: '500000.00', anticipo: null,
  fecha_recepcion: null, compra_id: null, factura_proveedor_id: null, condicion_pago: 'credito',
  origen_anticipo: null, origen_pago: null, notas: null,
  detalles: [{ id: 1, producto_id: 3, descripcion: 'Martillo', cantidad: '10', costo_estimado: '7000' }],
  horas_transcurridas: 20.0, lead_time_horas: null, promedio_proveedor_horas: 48.0,
}
const RECIBIDO = {
  ...EN_CAMINO, id: 9, estado: 'recibido', fecha_recepcion: '2026-07-09T10:00:00-05:00',
  compra_id: 42, condicion_pago: 'contado', horas_transcurridas: null, lead_time_horas: 20.0,
}
const METRICAS = [{
  proveedor_id: 1, proveedor_nombre: 'Ferrisariato', pedidos_recibidos: 3,
  lead_time_promedio_horas: 48.0, ultima_entrega: '2026-07-01T10:00:00-05:00',
  pedidos_en_camino: 1, mas_viejo_en_camino_horas: 20.0,
}]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ pedidos = [EN_CAMINO] } = {}) {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/pedidos-proveedor/metricas')) return Promise.resolve(jsonResp(METRICAS))
    if (u.includes('/recibir')) {
      return Promise.resolve(jsonResp({
        pedido: { ...EN_CAMINO, estado: 'recibido' }, compra_id: 1, lineas: [], replay: false,
      }))
    }
    if (u.includes('/corregir')) {
      return Promise.resolve(jsonResp({ compra: { id: 42 }, delta_total: '0', lineas: [] }))
    }
    if (u.includes('/pedidos-proveedor') && opts.method === 'POST') {
      return Promise.resolve(jsonResp({ ...EN_CAMINO, id: 8 }, 201))
    }
    if (u.includes('/pedidos-proveedor')) return Promise.resolve(jsonResp(pedidos))
    if (u.includes('/productos')) {
      return Promise.resolve(jsonResp([{ id: 3, nombre: 'Martillo', precio_compra: '7000' }]))
    }
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderTab(features = CON_CICLO) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>{conQuery(<TabCompras />)}</FeaturesProvider>
    </MemoryRouter>,
  )
}

async function abrirModalNuevaCompra() {
  fireEvent.click(await screen.findByText('Nueva compra'))
  fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Ferrisariato' } })
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
  fireEvent.click(await screen.findByText('Martillo'))
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Tab Compras — ciclo del pedido', () => {
  it('lista las compras en camino con cronómetro y lo que suele tardar el proveedor', async () => {
    instalarFetch()
    renderTab()

    expect((await screen.findAllByText('Ferrisariato')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/20 h/).length).toBeGreaterThan(0)   // cronómetro vivo
    expect(screen.getByText(/suele tardar/)).toBeInTheDocument()    // semáforo vs histórico
    expect(screen.getByText('¿Cuánto tarda cada proveedor?')).toBeInTheDocument()
  })

  it('el pedido exige productos con cantidad y costo unitario', async () => {
    instalarFetch()
    renderTab()
    fireEvent.click(await screen.findByText('Nueva compra'))
    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: 'Ferrisariato' } })

    // Sin líneas no se puede registrar.
    expect(screen.getByRole('button', { name: 'Registrar compra' })).toBeDisabled()
  })

  it('registra la compra con líneas, forma de pago y de dónde sale la plata', async () => {
    const fetchMock = instalarFetch()
    renderTab()
    await abrirModalNuevaCompra()

    fireEvent.change(screen.getByLabelText('Cantidad Martillo'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Costo unitario Martillo'), { target: { value: '7000' } })
    fireEvent.click(screen.getByText('Efectivo guardado'))       // no sale del cajón del día
    fireEvent.click(screen.getByRole('button', { name: 'Registrar compra' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        c => String(c[0]).includes('/pedidos-proveedor') && c[1]?.method === 'POST',
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        proveedor: { nombre: 'Ferrisariato' },
        condicion_pago: 'contado',
        origen_fondos: 'efectivo_externo',
        lineas: [{ producto_id: 3, cantidad: 10, costo_estimado: 7000 }],
      })
      expect(new Headers(call[1].headers).get('Idempotency-Key')).toBeTruthy()
    })
  })

  it('con anticipo parcial exige un monto menor al total', async () => {
    instalarFetch()
    renderTab()
    await abrirModalNuevaCompra()
    fireEvent.change(screen.getByLabelText('Cantidad Martillo'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Costo unitario Martillo'), { target: { value: '7000' } })

    fireEvent.click(screen.getByText('Anticipo + saldo'))
    fireEvent.change(screen.getByLabelText('Anticipo'), { target: { value: '70000' } })  // = total
    expect(screen.getByRole('button', { name: 'Registrar compra' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Anticipo'), { target: { value: '20000' } })
    expect(screen.getByRole('button', { name: 'Registrar compra' })).not.toBeDisabled()
  })

  it('pago MIXTO: reparte el monto entre medios y exige que las partes sumen el total', async () => {
    const fetchMock = instalarFetch()
    renderTab()
    await abrirModalNuevaCompra()
    fireEvent.change(screen.getByLabelText('Cantidad Martillo'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Costo unitario Martillo'), { target: { value: '7000' } })

    fireEvent.click(screen.getByText(/Pago mixto/))
    // Arranca con todo en el medio elegido (caja): repartir 30.000 al banco deja 40.000 en caja.
    fireEvent.change(screen.getByLabelText('Monto Efectivo de la caja'), { target: { value: '40000' } })
    expect(screen.getByRole('button', { name: 'Registrar compra' })).toBeDisabled()   // 40k de 70k

    fireEvent.change(screen.getByLabelText('Monto Transferencia / banco'), { target: { value: '30000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Registrar compra' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        c => String(c[0]).includes('/pedidos-proveedor') && c[1]?.method === 'POST',
      )
      expect(JSON.parse(call[1].body).pagos).toEqual([
        { origen: 'caja', monto: 40000 },
        { origen: 'banco', monto: 30000 },
      ])
    })
  })

  it('«Llegó» abre la recepción con las líneas del pedido prellenadas', async () => {
    const fetchMock = instalarFetch()
    renderTab()
    fireEvent.click(await screen.findByText('Llegó'))

    expect(await screen.findByText(/Llegó la mercancía/)).toBeInTheDocument()
    expect(screen.getByLabelText('Cantidad Martillo')).toHaveValue(10)
    expect(screen.getByLabelText('Costo unitario Martillo')).toHaveValue(7000)

    fireEvent.click(screen.getByRole('button', { name: 'Registrar llegada' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/recibir'))
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        lineas: [{ producto_id: 3, cantidad: 10, costo: 7000 }],
        condicion_pago: 'credito',      // la declarada al pedir viene prellenada
      })
    })
  })

  it('una compra recibida se puede corregir (POST /compras/{id}/corregir con motivo)', async () => {
    const fetchMock = instalarFetch({ pedidos: [RECIBIDO] })
    renderTab()

    fireEvent.click(await screen.findByText('Corregir'))
    expect(await screen.findByText(/Corregir compra/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Cantidad Martillo'), { target: { value: '12' } })
    fireEvent.change(screen.getByLabelText('Motivo'), { target: { value: 'llegaron 2 más' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar corrección' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/compras/42/corregir'))
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        lineas: [{ producto_id: 3, cantidad: 12, costo: 7000 }],
        motivo: 'llegaron 2 más',
        ajustar_pago: true,
      })
    })
  })

  it('un vendedor ve el ciclo pero no el botón de corregir (la corrección es de admin)', async () => {
    authState.admin = false
    instalarFetch({ pedidos: [RECIBIDO] })
    renderTab()

    expect((await screen.findAllByText('Ferrisariato')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Corregir')).toBeNull()
  })
})
