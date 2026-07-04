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

// Atajos de teclado del POS (ADR 0029). El listener vive en `document`: los eventos se disparan ahí.
const TORNILLO_COD = { id: 3, nombre: 'Tornillo', precio_venta: '150', codigo: '7701234567890' }

describe('TabVentasRapidas — atajos de teclado', () => {
  it('F2 enfoca el buscador', () => {
    instalarFetch()
    render(<TabVentasRapidas />)
    expect(screen.getByLabelText('Buscar producto')).not.toHaveFocus()

    fireEvent.keyDown(document, { key: 'F2' })
    expect(screen.getByLabelText('Buscar producto')).toHaveFocus()
  })

  it('«/» enfoca el buscador fuera de un campo, pero NO cuando escribes en uno', () => {
    instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.keyDown(document.body, { key: '/' })
    expect(screen.getByLabelText('Buscar producto')).toHaveFocus()

    // Con el foco en otro input, «/» debe teclearse (no robar el foco al buscador).
    const desc = screen.getByLabelText('Descripción varia')
    desc.focus()
    fireEvent.keyDown(desc, { key: '/' })
    expect(desc).toHaveFocus()
  })

  it('Enter en el buscador con resultados agrega el primer producto', async () => {
    instalarFetch()
    render(<TabVentasRapidas />)
    const input = screen.getByLabelText('Buscar producto')

    fireEvent.change(input, { target: { value: 'mar' } })
    await screen.findByText('Martillo')     // resultados listos
    input.focus()
    fireEvent.keyDown(document, { key: 'Enter' })

    await screen.findByLabelText('Cantidad de Martillo')  // agregado al carrito
  })

  it('F9 cobra (POST /ventas) y limpia el carrito', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))

    fireEvent.keyDown(document, { key: 'F9' })
    await screen.findByText('Agrega productos para vender.')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')).toBe(true)
  })

  it('Ctrl+Enter también cobra', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    fireEvent.click(await screen.findByText('Martillo'))

    fireEvent.keyDown(document, { key: 'Enter', ctrlKey: true })
    await screen.findByText('Agrega productos para vender.')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')).toBe(true)
  })

  it('Alt+2 selecciona «transferencia» como método de pago', () => {
    instalarFetch()
    render(<TabVentasRapidas />)

    fireEvent.keyDown(document, { key: '2', altKey: true })
    expect(screen.getByLabelText('Método de pago')).toHaveValue('transferencia')
  })

  it('lector de código de barras: ráfaga de teclas + Enter busca y agrega directo', async () => {
    const fetchMock = instalarFetch([TORNILLO_COD])
    render(<TabVentasRapidas />)

    const code = '7701234567890'
    for (const ch of code) fireEvent.keyDown(document, { key: ch })  // ráfaga (sin pausa → buffer intacto)
    fireEvent.keyDown(document, { key: 'Enter' })

    await screen.findByLabelText('Cantidad de Tornillo')  // agregado sin pasar por el buscador
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes(`/productos?q=${code}`))).toBe(true)
  })

  it('una ráfaga corta (menos del mínimo) NO se trata como escaneo', () => {
    const fetchMock = instalarFetch([TORNILLO_COD])
    render(<TabVentasRapidas />)

    fireEvent.keyDown(document, { key: '1' })
    fireEvent.keyDown(document, { key: '2' })
    fireEvent.keyDown(document, { key: 'Enter' })

    expect(screen.getByText('Agrega productos para vender.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=12'))).toBe(false)
  })
})
