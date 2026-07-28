/*
 * VistaMes — el calendario del mes con la forma del dashboard viejo.
 *
 * Se fija lo que puede engañar al dueño: que cada celda diga cuánto fue ese día, que los KPIs salgan
 * de los días CON venta (no de los 31), y sobre todo que lo anotado a mano NUNCA se sume con lo que
 * registró el sistema. Y que anotar un día solo se pueda donde tiene sentido: un día pasado y sin
 * ventas — sobre un día con ventas registradas no se escribe, sería inventar una segunda verdad
 * para la misma fecha.
 *
 * La fecha va mockeada: sin eso, "un día pasado" cambia de significado según el día en que corra la
 * suite, y el test sería verde por casualidad la mayor parte del mes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/fechas', () => ({
  hoyStrCO: () => '2026-07-28',
  anioMesCO: () => ({ anio: 2026, mes: 7 }),
}))

import { toast } from 'sonner'
import VistaMes from './VistaMes.jsx'

const dia = (d) => `2026-07-${String(d).padStart(2, '0')}`

const DIAS = [
  { fecha: dia(1), total: '1030500.00', num_ventas: 22, gastos: '0', historico: '0' },
  { fecha: dia(3), total: '500000.00', num_ventas: 4, gastos: '0', historico: '0' },
  // Día ANTERIOR al sistema: solo el total anotado a mano.
  { fecha: dia(9), total: '0', num_ventas: 0, gastos: '0', historico: '80000.00' },
]
const RESUMEN = {
  fecha: dia(28), num_ventas: 26, total_vendido: '1530500.00', ticket_promedio: '58865.00',
  por_metodo_pago: { efectivo: '1200000.00', transferencia: '330500.00' },
}

function sesion(rol = 'admin') {
  localStorage.setItem('ferrebot_token', 't')
  localStorage.setItem('ferrebot_user', JSON.stringify({ id: 1, rol }))
}

const jsonResp = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })

function instalarFetch(dias = DIAS, { cargarStatus = 200 } = {}) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/historico-ventas/cargar'))
      return Promise.resolve(jsonResp({ guardados: 1 }, cargarStatus))
    if (u.includes('/reportes/calendario')) return Promise.resolve(jsonResp(dias))
    if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('VistaMes — el calendario', () => {
  it('cada celda dice cuánto fue ese día, abreviado', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)

    // Sin la cifra en la celda había que pasar el mouse día por día para saber cuánto fue cada uno.
    expect(await screen.findByText('1,03M')).toBeInTheDocument()
    expect(screen.getByText('500k')).toBeInTheDocument()
    expect(screen.getByText('80,0k')).toBeInTheDocument()
  })

  it('los KPIs del mes salen de los días CON venta', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    expect(screen.getByText('$1.530.500')).toBeInTheDocument()   // total del mes
    expect(screen.getByText('$765.250')).toBeInTheDocument()     // promedio sobre esos 2 días
    expect(screen.getByText('$1.030.500')).toBeInTheDocument()   // mejor día
    // Acotado a su tarjeta: el "2" suelto también es la celda del día 2 del calendario.
    // KpiCard repite el rótulo (banda visible + versión accesible): basta con el primero.
    const tarjeta = screen.getAllByText(/Días con venta/i)[0].closest('.relative')
    expect(tarjeta).toHaveTextContent('2')
  })

  it('lo anotado a mano NO entra al total del mes', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    // $1.530.500 del sistema + $80.000 anotados darían $1.610.500: esa cifra no debe existir en
    // ninguna parte, porque de ese día no hay gastos que la respalden.
    expect(screen.queryByText('$1.610.500')).not.toBeInTheDocument()
    expect(screen.getByText(/Anotado a mano · \$80\.000/)).toBeInTheDocument()
  })

  it('explica qué son los días anotados a mano', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)

    expect(await screen.findByText(/no entra a los reportes financieros/i)).toBeInTheDocument()
  })

  it('sin días anotados, esa leyenda no aparece', async () => {
    sesion(); instalarFetch([DIAS[0]])
    render(<VistaMes />)
    await screen.findByText('1,03M')

    expect(screen.queryByText(/Anotado a mano ·/)).not.toBeInTheDocument()
  })

  it('muestra el desglose por método del mes', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    expect(screen.getByText('$1.200.000')).toBeInTheDocument()   // efectivo
    expect(screen.getByText('$330.500')).toBeInTheDocument()     // transferencia
    expect(screen.getByText('$0')).toBeInTheDocument()           // datáfono, sin movimiento
  })
})

describe('VistaMes — anotar un día a mano', () => {
  it('un día pasado sin ventas se puede tocar para anotar su total', async () => {
    sesion(); const fetchMock = instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    fireEvent.click(screen.getByRole('button', { name: `Anotar el total del ${dia(15)}` }))
    fireEvent.change(screen.getByLabelText(`Total del ${dia(15)}`), { target: { value: '345000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar' }))

    await waitFor(() => {
      const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/historico-ventas/cargar'))
      expect(llamada).toBeTruthy()
      expect(JSON.parse(llamada[1].body)).toEqual({ dias: [{ fecha: dia(15), total: 345000 }] })
    })
    expect(toast.success).toHaveBeenCalled()
  })

  it('un día CON ventas registradas no se puede anotar encima', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    // Ese número lo produjo el sistema: anotarlo encima sería una segunda verdad para la misma fecha.
    expect(screen.queryByRole('button', { name: `Anotar el total del ${dia(1)}` })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: `Anotar el total del ${dia(3)}` })).not.toBeInTheDocument()
  })

  it('un día futuro tampoco', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    // Hoy es el 28: el 30 todavía no pasó, anotarle un total sería inventar el futuro.
    expect(screen.queryByRole('button', { name: `Anotar el total del ${dia(30)}` })).not.toBeInTheDocument()
  })

  it('un día ya anotado se puede corregir, con su valor precargado', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('80,0k')

    fireEvent.click(screen.getByRole('button', { name: `Anotar el total del ${dia(9)}` }))
    expect(screen.getByLabelText(`Total del ${dia(9)}`)).toHaveValue('80000')
  })

  it('un vendedor no puede anotar nada', async () => {
    sesion('vendedor'); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    // El backend responde 403: ofrecer la celda y fallar al tocarla sería peor.
    expect(screen.queryByRole('button', { name: /Anotar el total/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Cargar varios días/ })).not.toBeInTheDocument()
  })

  it('Escape cierra la edición sin guardar', async () => {
    sesion(); const fetchMock = instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    fireEvent.click(screen.getByRole('button', { name: `Anotar el total del ${dia(15)}` }))
    fireEvent.keyDown(screen.getByLabelText(`Total del ${dia(15)}`), { key: 'Escape' })

    expect(screen.queryByLabelText(`Total del ${dia(15)}`)).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/historico-ventas/cargar'))).toBe(false)
  })

  it('la carga masiva sigue disponible para el atraso de meses', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText('1,03M')

    fireEvent.click(screen.getByRole('button', { name: /Cargar varios días/ }))
    expect(screen.getByLabelText('Días y totales')).toBeInTheDocument()
  })
})
