/*
 * VistaMes — el calendario del mes y los días anteriores al sistema.
 *
 * El heatmap no tenía ningún test. Lo que se fija acá es la separación que pidió el dueño: los días
 * anotados a mano se ven, pero NUNCA sumados a lo que el sistema registró. Si esos dos números se
 * mezclaran, el mes mostraría una cifra que ningún reporte financiero puede respaldar.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { toast } from 'sonner'
import VistaMes from './VistaMes.jsx'

const hoy = new Date()
const ANIO = hoy.getFullYear()
const MES = String(hoy.getMonth() + 1).padStart(2, '0')
const dia = (d) => `${ANIO}-${MES}-${String(d).padStart(2, '0')}`

const DIAS = [
  { fecha: dia(3), total: '500000.00', num_ventas: 4, gastos: '0', historico: '0' },
  // Un día ANTERIOR al sistema: solo el total anotado a mano.
  { fecha: dia(5), total: '0', num_ventas: 0, gastos: '0', historico: '1030500.00' },
]

function sesion(rol = 'admin') {
  localStorage.setItem('ferrebot_token', 't')
  localStorage.setItem('ferrebot_user', JSON.stringify({ id: 1, rol }))
}

const jsonResp = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })

function instalarFetch(dias = DIAS, { cargarStatus = 200 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/historico-ventas/cargar'))
      return Promise.resolve(jsonResp({ guardados: 3 }, cargarStatus))
    if (u.includes('/reportes/calendario')) return Promise.resolve(jsonResp(dias))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('VistaMes — calendario', () => {
  it('lo vendido y lo anotado a mano NUNCA salen sumados en un solo número', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)

    // $500.000 del sistema y $1.030.500 anotados: si se sumaran darían $1.530.500, que es
    // exactamente la cifra que no debe existir en ninguna parte.
    expect(await screen.findByText(/\$500\.000 vendidos/)).toBeInTheDocument()
    expect(screen.getByText(/\$1\.030\.500 anotados a mano/)).toBeInTheDocument()
    expect(screen.queryByText(/1\.530\.500/)).not.toBeInTheDocument()
  })

  it('explica qué son los días anotados a mano', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)

    // La leyenda no es decoración: sin ella, un día punteado es un misterio.
    expect(await screen.findByText(/No entra a los reportes financieros/)).toBeInTheDocument()
  })

  it('el detalle por día distingue el origen de cada cifra', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)

    expect(await screen.findByText('anotado a mano')).toBeInTheDocument()
    expect(screen.getByText(/4 ventas/)).toBeInTheDocument()
  })

  it('sin días anotados, la leyenda no aparece', async () => {
    sesion()
    instalarFetch([DIAS[0]])
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    expect(screen.queryByText(/anotados a mano/)).not.toBeInTheDocument()
  })

  it('solo un admin puede cargar días anteriores', async () => {
    sesion('vendedor'); instalarFetch()
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    // El backend responde 403: ofrecer el botón y fallar sería peor que no ofrecerlo.
    expect(screen.queryByRole('button', { name: /Cargar días anteriores/ })).not.toBeInTheDocument()
  })

  it('pegar la lista muestra la vista previa antes de guardar nada', async () => {
    sesion(); const fetchMock = instalarFetch()
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    fireEvent.click(screen.getByRole('button', { name: /Cargar días anteriores/ }))
    fireEvent.change(screen.getByLabelText('Días y totales'), {
      target: { value: '07/27\t$602.500\n07/24\t$283.000' },
    })

    // Específico a propósito: el calendario también tiene una celda con el texto '2'.
    expect(screen.getByText(/día\(s\) para guardar/)).toHaveTextContent('2 día(s) para guardar')
    expect(screen.getByText('$885.500')).toBeInTheDocument()   // la suma, antes de tocar la base
    // Y nada se guardó todavía: la vista previa es solo eso.
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/historico-ventas/cargar'))).toBe(false)
  })

  it('las líneas que no se entienden se muestran, no se descartan calladas', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    fireEvent.click(screen.getByRole('button', { name: /Cargar días anteriores/ }))
    fireEvent.change(screen.getByLabelText('Días y totales'), {
      target: { value: '07/27\t$602.500\nbasura de una fila mal copiada' },
    })

    // Descartar en silencio sería perder plata del reporte sin que nadie se entere.
    expect(screen.getByText(/1 línea\(s\) sin entender/)).toBeInTheDocument()
    expect(screen.getByText(/Línea 2:/)).toBeInTheDocument()
  })

  it('guardar manda los días parseados y refresca el calendario', async () => {
    sesion(); const fetchMock = instalarFetch()
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    fireEvent.click(screen.getByRole('button', { name: /Cargar días anteriores/ }))
    fireEvent.change(screen.getByLabelText('Días y totales'), {
      target: { value: '2026-07-27\t602.500' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Guardar/ }))

    await waitFor(() => {
      const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/historico-ventas/cargar'))
      expect(llamada).toBeTruthy()
      expect(JSON.parse(llamada[1].body)).toEqual({
        dias: [{ fecha: '2026-07-27', total: 602500 }],
      })
    })
    expect(toast.success).toHaveBeenCalled()
  })

  it('sin nada pegado no se puede guardar', async () => {
    sesion(); instalarFetch()
    render(<VistaMes />)
    await screen.findByText(/\$500\.000 vendidos/)

    fireEvent.click(screen.getByRole('button', { name: /Cargar días anteriores/ }))
    expect(screen.getByRole('button', { name: /Guardar/ })).toBeDisabled()
  })
})
