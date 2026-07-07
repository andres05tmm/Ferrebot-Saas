import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabHerramientas from './TabHerramientas.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const HERRAMIENTA = {
  id: 1, codigo: 'H-001', nombre: 'Pulidora Bosch', categoria: 'Eléctrica', cantidad: 3,
  ubicacion_actual: 'Bodega principal', estado: 'DISPONIBLE', valor_reposicion: '320000.0000', notas: null,
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabHerramientas /></MemoryRouter>) }

describe('TabHerramientas', () => {
  it('lista las herramientas con su estado como semáforo', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([HERRAMIENTA]))))
    render_()
    expect(await screen.findByText('Pulidora Bosch')).toBeInTheDocument()
    expect(screen.getAllByText('Disponible').length).toBeGreaterThan(0)   // semáforo (+ chip de filtro)
    expect(screen.getByText('H-001')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando no hay herramienta', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('Sin herramienta registrada')).toBeInTheDocument()
    expect(screen.getByText('Registrar la primera herramienta')).toBeInTheDocument()
  })

  it('crear herramienta postea el shape correcto (POST /herramientas)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (String(url).includes('/herramientas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2 }, 201))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nueva herramienta'))
    fireEvent.change(await screen.findByLabelText('Código'), { target: { value: 'H-010' } })
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Taladro Makita' } })
    fireEvent.change(screen.getByLabelText('Cantidad'), { target: { value: '2' } })
    fireEvent.click(screen.getByText('Crear herramienta'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/herramientas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        codigo: 'H-010', nombre: 'Taladro Makita', cantidad: 2, estado: 'DISPONIBLE',
      })
    })
  })

  it('la ruta /herramientas se gatea por la feature `herramientas` (y el meta-pack construccion)', () => {
    expect(isRouteEnabled('/herramientas', [])).toBe(false)
    expect(isRouteEnabled('/herramientas', ['herramientas'])).toBe(true)
    expect(isRouteEnabled('/herramientas', ['construccion'])).toBe(true)
  })
})
