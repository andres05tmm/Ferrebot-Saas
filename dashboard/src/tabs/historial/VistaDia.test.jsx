import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import VistaDia from './VistaDia.jsx'

const VENTAS = [{ id: 1, consecutivo: 5, vendedor_id: 5, fecha: '2026-06-05T15:00:00+00:00', total: '23800.00', metodo_pago: 'efectivo', estado: 'completada' }]
const DETALLE = {
  id: 1, consecutivo: 5, cliente_id: null, vendedor_id: 5, fecha: '2026-06-05T15:00:00+00:00',
  subtotal: '20000.00', impuestos: '3800.00', total: '23800.00', metodo_pago: 'efectivo',
  estado: 'completada', origen: 'web', idempotency_key: null,
  lineas: [{ producto_id: 1, descripcion: 'Martillo', cantidad: '2', precio_unitario: '11900.00', iva: 19 }],
}

// Helpers para construir ventas de HOY (Colombia) usando el instante actual.
const ahoraISO = () => new Date().toISOString()
const ventaHoy = (over = {}) => ({ id: 1, consecutivo: 10, vendedor_id: 5, fecha: ahoraISO(), total: '100', metodo_pago: 'efectivo', estado: 'completada', ...over })
const ventaVieja = (over = {}) => ({ id: 2, consecutivo: 9, vendedor_id: 5, fecha: '2020-01-01T12:00:00+00:00', total: '50', metodo_pago: 'efectivo', estado: 'completada', ...over })

function sesion({ id = 5, rol = 'vendedor' } = {}) {
  localStorage.setItem('ferrebot_token', 't')
  localStorage.setItem('ferrebot_user', JSON.stringify({ id, rol }))
}

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch(ventas = VENTAS, { deleteStatus = 200, putStatus = 200 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (/\/ventas\/\d+$/.test(u) && opts?.method === 'DELETE')
      return Promise.resolve({ ok: deleteStatus < 400, status: deleteStatus, json: async () => ({}) })
    if (/\/ventas\/\d+$/.test(u) && opts?.method === 'PUT')
      return Promise.resolve({ ok: putStatus < 400, status: putStatus, json: async () => DETALLE })
    if (/\/ventas\/\d+/.test(u)) return Promise.resolve(jsonResp(DETALLE))   // detalle (GET)
    if (u.includes('/ventas')) return Promise.resolve(jsonResp(ventas))        // lista
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('VistaDia (historial)', () => {
  it('lista las ventas del rango y al expandir pide el detalle con sus líneas', async () => {
    instalarFetch()
    render(<MemoryRouter><VistaDia /></MemoryRouter>)

    fireEvent.click(await screen.findByText('N.º 5'))           // expandir la venta
    expect(await screen.findByText('Martillo')).toBeInTheDocument()  // línea del detalle
  })

  it("un evento 'venta_registrada' dispara re-fetch de la lista", async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 5')

    const listaCalls = () => fetchMock.mock.calls.filter(c => /\/ventas\?/.test(String(c[0]))).length
    const antes = listaCalls()
    await act(async () => { rtHandler('venta_registrada') })
    expect(listaCalls()).toBeGreaterThan(antes)
  })
})

describe('VistaDia — borrar venta', () => {
  it('el botón borrar aparece solo para ventas de HOY propias (vieja/ajena sin botón)', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    const ajena = { id: 3, consecutivo: 8, vendedor_id: 99, fecha: ahoraISO(), total: '70', metodo_pago: 'efectivo', estado: 'completada' }
    instalarFetch([ventaHoy(), ventaVieja(), ajena])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    expect(screen.getByLabelText('Borrar venta N.º 10')).toBeInTheDocument()  // hoy + propia
    expect(screen.queryByLabelText('Borrar venta N.º 9')).toBeNull()          // día anterior → sin botón
    expect(screen.queryByLabelText('Borrar venta N.º 8')).toBeNull()          // ajena → sin botón
  })

  it('un admin ve el botón en ventas ajenas de hoy', async () => {
    sesion({ id: 1, rol: 'admin' })
    const ajenaHoy = { id: 3, consecutivo: 8, vendedor_id: 99, fecha: ahoraISO(), total: '70', metodo_pago: 'efectivo', estado: 'completada' }
    instalarFetch([ajenaHoy])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 8')

    expect(screen.getByLabelText('Borrar venta N.º 8')).toBeInTheDocument()
  })

  it('borrar postea DELETE /ventas/{id} tras confirmar', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const fetchMock = instalarFetch([ventaHoy()])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    fireEvent.click(screen.getByLabelText('Borrar venta N.º 10'))
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => /\/ventas\/1$/.test(String(c[0])) && c[1]?.method === 'DELETE')).toBe(true)
    })
  })

  it('NO borra si el usuario cancela la confirmación', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const fetchMock = instalarFetch([ventaHoy()])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    fireEvent.click(screen.getByLabelText('Borrar venta N.º 10'))
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false)
  })

  it('un 409 muestra el mensaje de factura electrónica', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    instalarFetch([ventaHoy()], { deleteStatus: 409 })
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    fireEvent.click(screen.getByLabelText('Borrar venta N.º 10'))
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Tiene factura electrónica, no se puede borrar')
    })
  })
})

describe('VistaDia — editar venta', () => {
  it('el control editar aparece solo para ventas de HOY propias (vieja/ajena sin botón)', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    const ajena = { id: 3, consecutivo: 8, vendedor_id: 99, fecha: ahoraISO(), total: '70', metodo_pago: 'efectivo', estado: 'completada' }
    instalarFetch([ventaHoy(), ventaVieja(), ajena])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    expect(screen.getByLabelText('Editar venta N.º 10')).toBeInTheDocument()
    expect(screen.queryByLabelText('Editar venta N.º 9')).toBeNull()
    expect(screen.queryByLabelText('Editar venta N.º 8')).toBeNull()
  })

  it('editar abre el form prellenado y postea PUT con el shape correcto', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    const fetchMock = instalarFetch([ventaHoy()])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    fireEvent.click(screen.getByLabelText('Editar venta N.º 10'))
    // El form carga del GET /ventas/1 (DETALLE) → línea Martillo prellenada con cantidad 2.
    const cantidad = await screen.findByLabelText('Cantidad línea 1')
    expect(cantidad).toHaveValue(2)
    fireEvent.change(cantidad, { target: { value: '5' } })
    fireEvent.click(screen.getByText('Guardar cambios'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => /\/ventas\/1$/.test(String(c[0])) && c[1]?.method === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        metodo_pago: 'efectivo',
        lineas: [{ producto_id: 1, cantidad: 5, precio_unitario: 11900 }],
      })
    })
  })

  it('un 409 al editar muestra el mensaje de factura electrónica', async () => {
    sesion({ id: 5, rol: 'vendedor' })
    instalarFetch([ventaHoy()], { putStatus: 409 })
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 10')

    fireEvent.click(screen.getByLabelText('Editar venta N.º 10'))
    fireEvent.click(await screen.findByText('Guardar cambios'))
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Tiene factura electrónica, no se puede editar')
    })
  })
})

describe('VistaDia — estado fiscal', () => {
  const ventaFiscal = (over = {}) => ({
    id: 1, consecutivo: 11, vendedor_id: 5, fecha: ahoraISO(), total: '100', metodo_pago: 'efectivo',
    estado: 'completada', fiscal: { tipo: 'factura', estado: 'rechazada', cufe: null, numero: 3, prefijo: 'FPR' }, ...over,
  })

  it('pinta el badge fiscal por venta; la venta sin fiscal no añade badge', async () => {
    instalarFetch([ventaFiscal(), ventaVieja()])             // ventaVieja no trae `fiscal`
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 11')

    const badge = screen.getByText(/Factura · rechazada/i)
    expect(badge).toHaveClass('text-destructive')            // rechazada → variante roja
    expect(screen.queryAllByText(/· rechazada/i)).toHaveLength(1)  // solo la venta con fiscal
  })

  it('el detalle expandible muestra el CUDE/CUFE y el número (prefijo-consecutivo)', async () => {
    const detalleFiscal = { ...DETALLE, fiscal: { tipo: 'pos', estado: 'aceptada', cufe: 'CUDE-ABC', numero: 7, prefijo: 'DPOS' } }
    const fetchMock = vi.fn((url) => {
      const u = String(url)
      if (/\/ventas\/\d+/.test(u)) return Promise.resolve(jsonResp(detalleFiscal))    // detalle (GET)
      if (u.includes('/ventas')) return Promise.resolve(jsonResp([ventaFiscal({ fiscal: detalleFiscal.fiscal })]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<MemoryRouter><VistaDia /></MemoryRouter>)

    fireEvent.click(await screen.findByText('N.º 11'))       // expandir
    expect(await screen.findByText(/CUDE-ABC/)).toBeInTheDocument()
    expect(screen.getByText(/CUDE:/)).toBeInTheDocument()    // POS → etiqueta CUDE (no CUFE)
    expect(screen.getByText(/DPOS-7/)).toBeInTheDocument()   // número = prefijo-consecutivo
  })

  it("un evento 'factura_aceptada' dispara re-fetch y la vista se suscribe a eventos fiscales", async () => {
    const fetchMock = instalarFetch([ventaFiscal()])
    render(<MemoryRouter><VistaDia /></MemoryRouter>)
    await screen.findByText('N.º 11')

    expect(rtEventos).toEqual(expect.arrayContaining(['factura_aceptada', 'factura_anulada']))
    const listaCalls = () => fetchMock.mock.calls.filter(c => /\/ventas\?/.test(String(c[0]))).length
    const antes = listaCalls()
    await act(async () => { rtHandler('factura_aceptada') })
    expect(listaCalls()).toBeGreaterThan(antes)
  })
})
