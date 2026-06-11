import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { FeaturesProvider } from '@/lib/features.jsx'
import TabVentasRapidas from './TabVentasRapidas.jsx'

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

const MARTILLO = { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }
// Producto con precio especial: habilita el selector Normal/Especial por línea.
const TALADRO_ESP = { id: 2, nombre: 'Taladro', precio_venta: '100000', precio_especial: '90000', unidad_medida: 'unidad' }

function instalarFetch(productos = [MARTILLO]) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/productos')) return Promise.resolve(jsonResp(productos))
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

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabVentasRapidas', () => {
  it('la búsqueda llama GET /productos?q', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await screen.findByText('Martillo')

    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=mar'))).toBe(true)
  })

  it('registrar hace POST /ventas con el shape VentaCrear + Idempotency-Key y limpia el carrito', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))      // agrega al carrito

    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')  // carrito limpio tras el éxito

    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')
    expect(call).toBeTruthy()
    expect(call[1].headers.get('Idempotency-Key')).toBeTruthy()
    const body = JSON.parse(call[1].body)
    expect(body).toMatchObject({ metodo_pago: 'efectivo', origen: 'web', lineas: [{ producto_id: 1, cantidad: 1 }] })
  })

  it('un producto SIN precio_especial no muestra el selector y postea { producto_id, cantidad }', async () => {
    const fetchMock = instalarFetch()  // Martillo: sin precio_especial
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))

    expect(screen.queryByRole('button', { name: /precio especial/i })).toBeNull()

    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).lineas).toEqual([{ producto_id: 1, cantidad: 1 }])
  })

  it('un producto CON precio_especial muestra ambos precios; "especial" envía precio_unitario override', async () => {
    const fetchMock = instalarFetch([TALADRO_ESP])
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'tal' } })
    fireEvent.click(await screen.findByText('Taladro'))

    // El selector muestra ambos valores (normal y especial).
    expect(screen.getByRole('button', { name: /precio normal de taladro/i })).toHaveTextContent('$100.000')
    const especial = screen.getByRole('button', { name: /precio especial de taladro/i })
    expect(especial).toHaveTextContent('$90.000')

    fireEvent.click(especial)                                 // elegir especial
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')

    // Override explícito por línea con el precio especial.
    expect(ventaBody(fetchMock).lineas).toEqual([{ producto_id: 2, cantidad: 1, precio_unitario: 90000 }])
  })

  it('elegir "normal" (incluso tras tocar especial) NO manda override', async () => {
    const fetchMock = instalarFetch([TALADRO_ESP])
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'tal' } })
    fireEvent.click(await screen.findByText('Taladro'))

    fireEvent.click(screen.getByRole('button', { name: /precio especial de taladro/i }))  // especial…
    fireEvent.click(screen.getByRole('button', { name: /precio normal de taladro/i }))    // …y de vuelta a normal

    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')

    expect(ventaBody(fetchMock).lineas).toEqual([{ producto_id: 2, cantidad: 1 }])  // sin precio_unitario
  })
})

// Selector de documento por venta (F2.3b, ADR 0014): se gatea por capacidades del tenant.
function renderCon(features, productos = [MARTILLO]) {
  const fetchMock = instalarFetch(productos)
  render(
    <FeaturesProvider features={features}>
      <TabVentasRapidas />
    </FeaturesProvider>,
  )
  return fetchMock
}

async function agregarMartillo(fetchMock) {
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
  fireEvent.click(await screen.findByText('Martillo'))
  return fetchMock
}

describe('TabVentasRapidas — selector de documento', () => {
  it('sin capacidad fiscal NO renderiza el selector ni manda `documento`', async () => {
    const fetchMock = renderCon([])  // ni pos_electronico ni facturacion_electronica
    expect(screen.queryByLabelText('Documento fiscal')).toBeNull()

    await agregarMartillo(fetchMock)
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).documento).toBeUndefined()
  })

  it('con ambas capacidades: selector con default POS; el payload manda documento="pos"', async () => {
    const fetchMock = renderCon(['pos_electronico', 'facturacion_electronica'])
    const pos = screen.getByRole('button', { name: /documento pos/i })
    const fe = screen.getByRole('button', { name: /documento factura/i })
    expect(pos).toHaveAttribute('aria-pressed', 'true')   // default POS
    expect(fe).toHaveAttribute('aria-pressed', 'false')

    await agregarMartillo(fetchMock)
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).documento).toBe('pos')
  })

  it('elegir "Factura" manda documento="fe" y muestra la nota de cliente', async () => {
    const fetchMock = renderCon(['pos_electronico', 'facturacion_electronica'])
    fireEvent.click(screen.getByRole('button', { name: /documento factura/i }))
    expect(screen.getByText(/consumidor final/i)).toBeInTheDocument()  // nota junto al ClientePicker

    await agregarMartillo(fetchMock)
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).documento).toBe('fe')
  })

  it('solo pos_electronico: estado fijo POS (sin botón Factura) y manda documento="pos"', async () => {
    const fetchMock = renderCon(['pos_electronico'])
    expect(screen.queryByRole('button', { name: /documento factura/i })).toBeNull()
    expect(screen.getByLabelText('Documento fiscal')).toHaveTextContent('POS')

    await agregarMartillo(fetchMock)
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).documento).toBe('pos')
  })

  it('solo facturacion_electronica: estado fijo Factura, default fe, nota visible y manda documento="fe"', async () => {
    const fetchMock = renderCon(['facturacion_electronica'])
    expect(screen.queryByRole('button', { name: /documento pos/i })).toBeNull()
    expect(screen.getByLabelText('Documento fiscal')).toHaveTextContent(/factura/i)
    expect(screen.getByText(/consumidor final/i)).toBeInTheDocument()

    await agregarMartillo(fetchMock)
    fireEvent.click(screen.getByText('Registrar venta'))
    await screen.findByText('Agrega productos para vender.')
    expect(ventaBody(fetchMock).documento).toBe('fe')
  })
})
