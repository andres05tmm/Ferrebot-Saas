import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
// Tenant con capacidad fiscal completa (POS + FE) → se muestra el selector de documento.
vi.mock('@/lib/features.jsx', () => ({ useFeatures: () => ['pos_electronico', 'facturacion_electronica'] }))

import TabVentasRapidas from './TabVentasRapidas.jsx'
import { PreferenciasProvider } from '@/lib/preferencias.jsx'

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }
const MARTILLO = { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
    if (/\/productos\/\d+\/precio/.test(u)) {
      const id = Number(u.match(/\/productos\/(\d+)\/precio/)[1])
      return Promise.resolve(jsonResp({ producto_id: id, cantidad: 1, precio_unitario: '10000', total: '10000', regla: 'x' }))
    }
    if (u.includes('/productos')) return Promise.resolve(jsonResp([MARTILLO]))
    if (u.includes('/clientes')) return Promise.resolve(jsonResp([]))
    if (u.includes('/ventas')) return Promise.resolve(jsonResp({ id: 9, consecutivo: 1 }))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function ventaBody(fetchMock) {
  const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')
  expect(call).toBeTruthy()
  return JSON.parse(call[1].body)
}

async function agregarMartillo() {
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
  fireEvent.click(await screen.findByText('Martillo'))
}

function renderConAutoFacturar(facturarEnVenta) {
  return render(
    <PreferenciasProvider facturarEnVenta={facturarEnVenta}>
      <TabVentasRapidas />
    </PreferenciasProvider>,
  )
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabVentasRapidas — documento con facturar_en_venta=false', () => {
  it('ofrece "Sin factura" y la venta NO manda documento por defecto', async () => {
    const fetchMock = instalarFetch()
    renderConAutoFacturar(false)
    // La opción "Sin factura" existe (default).
    expect(screen.getByLabelText('Documento Sin factura')).toBeInTheDocument()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)
    expect(ventaBody(fetchMock).documento).toBeUndefined()   // venta interna: sin intención fiscal
  })

  it('elegir POS a pedido SÍ manda documento="pos" aunque el toggle esté off', async () => {
    const fetchMock = instalarFetch()
    renderConAutoFacturar(false)
    await agregarMartillo()
    fireEvent.click(screen.getByLabelText('Documento POS'))    // opt-in explícito
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)
    expect(ventaBody(fetchMock).documento).toBe('pos')
  })
})

describe('TabVentasRapidas — documento con facturar_en_venta=true (histórico)', () => {
  it('sin opción "Sin factura" y la venta manda documento="pos" por defecto', async () => {
    const fetchMock = instalarFetch()
    renderConAutoFacturar(true)
    expect(screen.queryByLabelText('Documento Sin factura')).toBeNull()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)
    expect(ventaBody(fetchMock).documento).toBe('pos')       // auto-factura por defecto
  })
})
