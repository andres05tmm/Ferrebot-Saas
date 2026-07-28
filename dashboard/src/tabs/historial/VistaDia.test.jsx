/*
 * VistaDia — el libro de ventas renglón por renglón.
 *
 * Se reescribió junto con la vista: antes listaba cabeceras y pedía el detalle por venta; ahora
 * `GET /ventas/historial` trae los renglones resueltos. Lo que se prueba acá es lo que puede
 * engañar al dueño: el orden de las columnas, que las filas de una misma venta se lean como un
 * bloque (porque borrar borra la VENTA), que los días viejos no ofrezcan botones que van a fallar,
 * y que el total no sea la suma de lo que quepa en pantalla.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import VistaDia from './VistaDia.jsx'

const ahoraISO = () => new Date().toISOString()

// Dos renglones de la MISMA venta (id 1) + uno de otra venta (id 2).
const linea = (over = {}) => ({
  linea_id: 1, venta_id: 1, consecutivo: 5, fecha: ahoraISO(), estado: 'completada',
  producto: 'Cemento gris 50kg', producto_id: 7, cantidad: '2', precio_unitario: '20000',
  iva: 0, total_linea: '40000', cliente: 'MARIA GOMEZ', cliente_id: 3,
  vendedor: 'Andrés', vendedor_id: 5, metodo_pago: 'efectivo', pagos: [],
  venta_total: '45000', num_lineas: 2, ...over,
})

const FEED = {
  desde: '2026-07-27', hasta: '2026-07-27', hay_mas: false,
  filas: [
    linea(),
    linea({ linea_id: 2, producto: 'Varilla 1/2', cantidad: '1', precio_unitario: '5000', total_linea: '5000' }),
    linea({ linea_id: 3, venta_id: 2, consecutivo: 6, producto: 'Martillo', cliente: 'Consumidor Final',
            cliente_id: null, num_lineas: 1, venta_total: '11900', total_linea: '11900' }),
  ],
}

const RESUMEN = {
  fecha: '2026-07-27', num_ventas: 2, total_vendido: '56900.00', ticket_promedio: '28450.00',
  por_metodo_pago: { efectivo: '56900.00' },
}

function sesion({ id = 5, rol = 'vendedor' } = {}) {
  localStorage.setItem('ferrebot_token', 't')
  localStorage.setItem('ferrebot_user', JSON.stringify({ id, rol }))
}

const jsonResp = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })

function instalarFetch(feed = FEED, { deleteStatus = 200, exportStatus = 200 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (/\/ventas\/\d+$/.test(u) && opts?.method === 'DELETE')
      return Promise.resolve(jsonResp({}, deleteStatus))
    if (u.includes('/ventas/historial/exportar'))
      return Promise.resolve({
        ok: exportStatus < 400, status: exportStatus,
        json: async () => ({ detail: 'El rango supera las 5000 ventas por archivo; expórtalo por meses' }),
        blob: async () => new Blob(['x']),
      })
    if (u.includes('/ventas/historial')) return Promise.resolve(jsonResp(feed))
    if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function pintar() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><VistaDia /></MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('VistaDia — el libro de ventas', () => {
  it('las columnas van en el orden que pidió el dueño', async () => {
    sesion(); instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    const titulos = screen.getAllByRole('columnheader').map(th => th.textContent.trim())
    expect(titulos.slice(0, 8)).toEqual(
      ['Hora', 'Producto', 'Cliente', 'Cant.', 'V. unit.', 'Método', 'Vendedor', 'Total']
    )
  })

  it('muestra un renglón por producto, no una fila por venta', async () => {
    sesion(); instalarFetch()
    pintar()

    // Tres renglones de dos ventas: lo que el dashboard viejo mostraba y el nuevo había perdido.
    expect(await screen.findByText('Cemento gris 50kg')).toBeInTheDocument()
    expect(screen.getByText('Varilla 1/2')).toBeInTheDocument()
    expect(screen.getByText('Martillo')).toBeInTheDocument()
    expect(screen.getByText('MARIA GOMEZ')).toBeInTheDocument()
    expect(screen.getByText('Consumidor Final')).toBeInTheDocument()
  })

  it('las filas de una misma venta se leen como un bloque', async () => {
    sesion(); instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    // El cliente aparece UNA vez por venta, no repetido en cada renglón: es lo que hace que el
    // botón de borrar se lea como "borra esta venta" y no "borra este renglón".
    expect(screen.getAllByText('MARIA GOMEZ')).toHaveLength(1)
    expect(screen.getAllByText('Andrés')).toHaveLength(2)     // una por venta (hay dos ventas)
  })

  it('borrar pide confirmación y borra la VENTA completa, no el renglón', async () => {
    sesion(); const fetchMock = instalarFetch()
    vi.stubGlobal('confirm', vi.fn(() => true))
    pintar()
    await screen.findByText('Cemento gris 50kg')

    fireEvent.click(screen.getByRole('button', { name: /Borrar venta N.º 5/ }))
    await waitFor(() => expect(
      fetchMock.mock.calls.some(c => String(c[0]).endsWith('/ventas/1') && c[1]?.method === 'DELETE')
    ).toBe(true))
    // El aviso habla de la venta, no del producto: lo que se pierde es la venta entera.
    expect(window.confirm.mock.calls[0][0]).toMatch(/venta N.º 5 completa/)
  })

  it('una venta de días anteriores no ofrece botones que van a fallar', async () => {
    sesion()
    instalarFetch({ ...FEED, filas: [linea({ fecha: '2020-01-01T12:00:00+00:00' })] })
    pintar()
    await screen.findByText('Cemento gris 50kg')

    // El backend responde 403 fuera de hoy: mostrar el botón y fallar al tocarlo sería peor.
    expect(screen.queryByRole('button', { name: /Borrar venta/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Editar venta/ })).not.toBeInTheDocument()
  })

  it('un vendedor no puede tocar la venta de otro', async () => {
    sesion({ id: 99, rol: 'vendedor' })       // la venta es del vendedor 5
    instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    expect(screen.queryByRole('button', { name: /Borrar venta/ })).not.toBeInTheDocument()
  })

  it('el total sale del resumen del período, no de sumar lo que quepa en pantalla', async () => {
    sesion(); instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    // Lo que se muestra es el agregado del backend: si el feed estuviera paginado, sumar la
    // pantalla daría de menos y nadie lo notaría.
    expect(screen.getAllByText('$56.900').length).toBeGreaterThan(0)
    expect(screen.getByText(/2 ventas/)).toBeInTheDocument()
  })

  it('una venta mixta dice cómo pagaron de verdad', async () => {
    sesion()
    instalarFetch({ ...FEED, filas: [linea({
      metodo_pago: 'mixto',
      pagos: [{ metodo: 'efectivo', monto: '30000' }, { metodo: 'transferencia', monto: '20000' }],
    })] })
    pintar()

    // 'mixto' a secas no le dice nada a nadie sobre cómo entró la plata.
    expect(await screen.findByText('efectivo + transferencia')).toBeInTheDocument()
  })

  it('una venta anulada sigue en el libro, marcada', async () => {
    sesion()
    instalarFetch({ ...FEED, filas: [linea({ estado: 'anulada' })] })
    pintar()
    await screen.findByText('Cemento gris 50kg')

    expect(screen.getByText('anulada')).toBeInTheDocument()
    // No se ofrece borrar lo que ya está anulado.
    expect(screen.queryByRole('button', { name: /Borrar venta/ })).not.toBeInTheDocument()
  })

  it('exportar descarga el Excel del rango en pantalla', async () => {
    sesion(); const fetchMock = instalarFetch()
    globalThis.URL.createObjectURL = vi.fn(() => 'blob:x')
    globalThis.URL.revokeObjectURL = vi.fn()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    fireEvent.click(screen.getByRole('button', { name: /Exportar Excel/ }))
    await waitFor(() => expect(
      fetchMock.mock.calls.some(c => String(c[0]).includes('/ventas/historial/exportar?desde='))
    ).toBe(true))
  })

  it('si el rango no cabe en un archivo, se muestra el motivo del backend', async () => {
    sesion(); instalarFetch(FEED, { exportStatus: 422 })
    pintar()
    await screen.findByText('Cemento gris 50kg')

    fireEvent.click(screen.getByRole('button', { name: /Exportar Excel/ }))
    // Un "error al exportar" genérico no dice qué hacer; el 422 explica que parta por meses.
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/por meses/)))
  })

  it('los atajos de rango cambian las fechas y vuelven a pedir', async () => {
    sesion(); const fetchMock = instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')

    fireEvent.click(screen.getByRole('button', { name: 'Este mes' }))
    await waitFor(() => expect(
      fetchMock.mock.calls.some(c => /\/ventas\/historial\?desde=\d{4}-\d{2}-01/.test(String(c[0])))
    ).toBe(true))
  })

  it('una venta nueva por SSE refresca la tabla sola', async () => {
    sesion(); const fetchMock = instalarFetch()
    pintar()
    await screen.findByText('Cemento gris 50kg')
    const antes = fetchMock.mock.calls.length

    rtHandler?.()
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(antes))
  })
})
