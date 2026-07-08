import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabObras from './TabObras.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const OBRA = {
  id: 7, cliente_id: 3, cliente_nombre: 'Alcaldía de La Estrella', nombre: 'Pavimentación vía La Estrella',
  ubicacion: 'La Estrella', fecha_inicio: '2026-07-01', fecha_fin_estimada: '2026-09-30',
  fecha_fin_real: null, estado: 'PLANIFICADA', notas: null, cotizacion_id: null,
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabObras /></MemoryRouter>) }

describe('TabObras', () => {
  it('lista las obras con su estado como semáforo', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      const u = String(url)
      if (u.includes('/obras')) return Promise.resolve(jsonResp([OBRA]))
      return Promise.resolve(jsonResp([]))
    }))
    render_()
    expect(await screen.findByText('Pavimentación vía La Estrella')).toBeInTheDocument()
    expect(screen.getAllByText('Planificada').length).toBeGreaterThan(0)   // semáforo (+ chip de filtro)
    expect(screen.getByText('Alcaldía de La Estrella')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando no hay obras', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('Todavía no hay obras')).toBeInTheDocument()
    expect(screen.getByText('Crear la primera obra')).toBeInTheDocument()
  })

  it('crear obra postea el shape correcto (POST /obras)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/obras') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }, 201))
      if (u.includes('/obras')) return Promise.resolve(jsonResp([]))
      if (u.includes('/clientes')) return Promise.resolve(jsonResp([{ id: 3, nombre: 'Alcaldía de La Estrella' }]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nueva obra'))
    fireEvent.change(await screen.findByLabelText('Nombre de la obra'), { target: { value: 'Vía nueva' } })
    fireEvent.change(await screen.findByLabelText('Cliente'), { target: { value: '3' } })
    fireEvent.click(screen.getByText('Crear obra'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/obras') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ nombre: 'Vía nueva', cliente_id: 3, ubicacion: null, notas: null })
    })
  })

  it('al expandir, transiciona el estado por el endpoint dedicado (PATCH /obras/{id}/estado)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      // El sub-recurso de reportes responde array plano (GET /obras/{id}/reportes-diarios).
      if (u.includes('/obras/7/reportes-diarios')) return Promise.resolve(jsonResp([]))
      if (u.includes('/obras/7/estado') && opts?.method === 'PATCH') return Promise.resolve(jsonResp({ ...OBRA, estado: 'EN_EJECUCION' }))
      if (u.includes('/obras/7')) return Promise.resolve(jsonResp({ ...OBRA, reportes_diarios: 0 }))
      if (u.includes('/obras')) return Promise.resolve(jsonResp([OBRA]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Pavimentación vía La Estrella'))   // expande la fila
    fireEvent.click(await screen.findByText('Iniciar ejecución'))

    await waitFor(() => {
      // La transición pega al endpoint dedicado /estado (no al PATCH de metadatos), con {estado}.
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/obras/7/estado') && c[1]?.method === 'PATCH')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({ estado: 'EN_EJECUCION' })
    })
    expect(await screen.findByText('Sin reportes de campo todavía')).toBeInTheDocument()
  })

  it('la ruta /obras se gatea por la feature `obras` (y el meta-pack construccion)', () => {
    expect(isRouteEnabled('/obras', [])).toBe(false)
    expect(isRouteEnabled('/obras', ['obras'])).toBe(true)
    expect(isRouteEnabled('/obras', ['construccion'])).toBe(true)
  })
})
