import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabMaquinas from './TabMaquinas.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const MAQUINA = {
  id: 1, codigo: 'M-001', nombre: 'Vibrocompactador CAT CS533E', tipo: 'Vibrocompactador',
  placa: 'ABC123', serial: null, anio_fabricacion: 2019, estado: 'DISPONIBLE',
  precio_hora_default: '180000.0000', minimo_horas_factura: 5, costo_operacion_hora: '60000.0000', notas: null,
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabMaquinas /></MemoryRouter>) }

describe('TabMaquinas', () => {
  it('lista las máquinas con su estado como semáforo', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([MAQUINA]))))
    render_()
    expect(await screen.findByText('Vibrocompactador CAT CS533E')).toBeInTheDocument()
    expect(screen.getAllByText('Disponible').length).toBeGreaterThan(0)   // semáforo (+ chip de filtro)
    expect(screen.getByText('M-001')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando el parque está vacío', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('El parque está vacío')).toBeInTheDocument()
    expect(screen.getByText('Registrar la primera máquina')).toBeInTheDocument()
  })

  it('crear máquina postea el shape correcto (POST /maquinas)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (String(url).includes('/maquinas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2 }, 201))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nueva máquina'))
    fireEvent.change(await screen.findByLabelText('Código'), { target: { value: 'M-010' } })
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Retroexcavadora' } })
    fireEvent.change(screen.getByLabelText('Tipo'), { target: { value: 'Retro' } })
    fireEvent.change(screen.getByLabelText('Precio por hora'), { target: { value: '250000' } })
    fireEvent.click(screen.getByText('Crear máquina'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/maquinas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        codigo: 'M-010', nombre: 'Retroexcavadora', tipo: 'Retro',
        precio_hora_default: 250000, minimo_horas_factura: 1,
      })
    })
  })

  it('la ruta /maquinas se gatea por la feature `maquinaria` (y el meta-pack construccion)', () => {
    expect(isRouteEnabled('/maquinas', [])).toBe(false)
    expect(isRouteEnabled('/maquinas', ['maquinaria'])).toBe(true)
    expect(isRouteEnabled('/maquinas', ['construccion'])).toBe(true)
  })
})
