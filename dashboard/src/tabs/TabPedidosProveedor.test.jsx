import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabPedidosProveedor from './TabPedidosProveedor.jsx'
import { conQuery } from '@/test/query.jsx'

const EN_CAMINO = {
  id: 7, proveedor_id: 1, proveedor_nombre: 'Ferrisariato',
  fecha_pedido: '2026-07-08T14:00:00-05:00', fecha_estimada: null, estado: 'pedido',
  descripcion: '50 martillos', monto_estimado: '500000.00', anticipo: null,
  fecha_recepcion: null, compra_id: null, factura_proveedor_id: null, condicion_pago: null,
  notas: null, detalles: [], horas_transcurridas: 20.0, lead_time_horas: null,
  promedio_proveedor_horas: 48.0,
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
    if (u.includes('/recibir')) return Promise.resolve(jsonResp({ pedido: { ...EN_CAMINO, estado: 'recibido' }, compra_id: 1, lineas: [], replay: false }))
    if (u.includes('/pedidos-proveedor') && opts.method === 'POST') return Promise.resolve(jsonResp({ ...EN_CAMINO, id: 8 }, 201))
    if (u.includes('/pedidos-proveedor')) return Promise.resolve(jsonResp(pedidos))
    if (u.includes('/productos')) return Promise.resolve(jsonResp([{ id: 3, nombre: 'Martillo', precio_compra: '7000' }]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabPedidosProveedor', () => {
  it('lista los pedidos en camino con cronómetro y promedio del proveedor', async () => {
    instalarFetch()
    render(conQuery(<TabPedidosProveedor />))

    expect((await screen.findAllByText('Ferrisariato')).length).toBeGreaterThan(0)
    expect(screen.getByText(/50 martillos/)).toBeInTheDocument()
    expect(screen.getAllByText(/20 h/).length).toBeGreaterThan(0)    // cronómetro vivo (fila + KPI)
    expect(screen.getByText(/suele tardar/)).toBeInTheDocument()     // semáforo vs histórico
  })

  it('registrar pedido manda POST con proveedor, descripción y anticipo', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<TabPedidosProveedor />))
    await screen.findAllByText('Ferrisariato')

    fireEvent.click(screen.getByRole('button', { name: /Registrar pedido/ }))
    fireEvent.change(await screen.findByLabelText('Proveedor'), { target: { value: 'Eternit' } })
    fireEvent.change(screen.getByLabelText('¿Qué se pidió?'), { target: { value: '10 tejas' } })
    fireEvent.change(screen.getByLabelText(/Anticipo/), { target: { value: '80000' } })
    fireEvent.click(screen.getByRole('button', { name: /^Registrar pedido$/ }))
    await screen.findAllByText('Ferrisariato')

    const post = fetchMock.mock.calls.find(
      c => String(c[0]).endsWith('/pedidos-proveedor') && c[1]?.method === 'POST',
    )
    expect(post).toBeTruthy()
    const body = JSON.parse(post[1].body)
    expect(body.proveedor).toEqual({ nombre: 'Eternit' })
    expect(body.descripcion).toBe('10 tejas')
    expect(body.anticipo).toBe(80000)
    expect(body.anticipo_desde_caja).toBe(true)
    expect(post[1].headers['Idempotency-Key'] || post[1].headers.get?.('Idempotency-Key')).toBeTruthy()
  })

  it('"Llegó" abre la recepción y manda líneas reales + condición de pago + cuadre', async () => {
    const fetchMock = instalarFetch()
    render(conQuery(<TabPedidosProveedor />))
    await screen.findAllByText('Ferrisariato')

    fireEvent.click(screen.getByRole('button', { name: /Llegó/ }))
    await screen.findByText(/Llegó la mercancía/)

    // agrega un producto real que llegó
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))
    fireEvent.change(screen.getByLabelText('Cantidad recibida Martillo'), { target: { value: '50' } })
    fireEvent.change(screen.getByLabelText('Costo real Martillo'), { target: { value: '7000' } })
    // cuadre de inventario progresivo (se prellenó con la cantidad)
    fireEvent.click(screen.getByRole('checkbox', { name: /Cuadrar inventario/ }))
    // a crédito con factura
    fireEvent.click(screen.getByRole('button', { name: /A crédito/ }))
    fireEvent.change(screen.getByLabelText(/Nº factura/), { target: { value: 'F-99' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar llegada/ }))
    await screen.findAllByText('Ferrisariato')

    const post = fetchMock.mock.calls.find(c => String(c[0]).includes('/recibir'))
    expect(post).toBeTruthy()
    const body = JSON.parse(post[1].body)
    expect(body.lineas).toEqual([
      { producto_id: 3, cantidad: 50, costo: 7000, cantidad_fisica: 50 },
    ])
    expect(body.condicion_pago).toBe('credito')
    expect(body.numero_factura).toBe('F-99')
  })

  it('muestra la tabla de lead time por proveedor', async () => {
    instalarFetch()
    render(conQuery(<TabPedidosProveedor />))

    expect(await screen.findByText('¿Cuánto tarda cada proveedor?')).toBeInTheDocument()
    expect(screen.getByText('2.0 días')).toBeInTheDocument()   // 48h promedio
  })
})
