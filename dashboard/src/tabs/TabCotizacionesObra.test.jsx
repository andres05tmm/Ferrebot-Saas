import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabCotizacionesObra from './TabCotizacionesObra.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const RESUMEN = {
  id: 7, numero: 'PIM-001-2026', cliente_id: 3, nombre_obra: 'Pavimentación vía La Estrella',
  ubicacion: 'La Estrella', fecha_emision: '2026-07-01T12:00:00Z', vigencia_dias: 15,
  estado: 'BORRADOR', creado_en: '2026-07-01T12:00:00Z', actualizado_en: '2026-07-01T12:00:00Z',
  total: '11276000.00',
}

const DETALLE_GANADA = {
  ...RESUMEN, estado: 'GANADA',
  administracion_pct: '0.05', imprevistos_pct: '0.03', utilidad_pct: '0.04', iva_sobre_utilidad_pct: '0.19',
  condiciones: null,
  items: [{ id: 1, orden: 1, descripcion: 'Base granular', unidad: 'm3', cantidad: '1000.0000', valor_unitario: '10000.0000', subtotal: '10000000.0000', costo_material_est: null, costo_mano_obra_est: null, costo_equipo_est: null }],
  totales: { subtotal: '10000000.00', administracion: '500000.00', imprevistos: '300000.00', utilidad: '400000.00', iva_utilidad: '76000.00', total: '11276000.00' },
}

beforeEach(() => { localStorage.clear(); vi.stubGlobal('confirm', vi.fn(() => true)) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabCotizacionesObra /></MemoryRouter>) }

describe('TabCotizacionesObra', () => {
  it('lista las cotizaciones con número, estado como semáforo y total', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      const u = String(url)
      if (u.includes('/cotizaciones-obra')) return Promise.resolve(jsonResp([RESUMEN]))
      return Promise.resolve(jsonResp([]))
    }))
    render_()
    expect(await screen.findByText('Pavimentación vía La Estrella')).toBeInTheDocument()
    expect(screen.getByText('PIM-001-2026')).toBeInTheDocument()
    expect(screen.getAllByText('Borrador').length).toBeGreaterThan(0)   // semáforo (+ chip)
    expect(screen.getByText('$11.276.000')).toBeInTheDocument()          // total del contrato
  })

  it('muestra un estado vacío con propósito cuando no hay cotizaciones', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('Todavía no hay cotizaciones')).toBeInTheDocument()
    expect(screen.getByText('Crear la primera cotización')).toBeInTheDocument()
  })

  it('el builder recalcula el desglose AIU en vivo (IVA sólo sobre la utilidad)', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      const u = String(url)
      if (u.includes('/clientes')) return Promise.resolve(jsonResp([{ id: 3, nombre: 'Alcaldía de La Estrella' }]))
      return Promise.resolve(jsonResp([]))
    }))
    render_()
    fireEvent.click(await screen.findByText('Nueva cotización'))
    fireEvent.change(await screen.findByLabelText('Descripción ítem 1'), { target: { value: 'Base granular' } })
    fireEvent.change(screen.getByLabelText('Unidad ítem 1'), { target: { value: 'm3' } })
    fireEvent.change(screen.getByLabelText('Cantidad ítem 1'), { target: { value: '1000' } })
    fireEvent.change(screen.getByLabelText('Valor unitario ítem 1'), { target: { value: '10000' } })
    fireEvent.change(screen.getByLabelText('Administración %'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Imprevistos %'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Utilidad %'), { target: { value: '4' } })
    // IVA queda en su default (19). Total esperado del plan: 11.276.000.
    expect(await screen.findByText('$11.276.000')).toBeInTheDocument()
  })

  it('crear postea el shape correcto: ítems + porcentajes AIU como fracción, sin número (POST /cotizaciones-obra)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/cotizaciones-obra') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }, 201))
      if (u.includes('/clientes')) return Promise.resolve(jsonResp([{ id: 3, nombre: 'Alcaldía de La Estrella' }]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nueva cotización'))
    fireEvent.change(await screen.findByLabelText('Cliente'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Nombre de la obra'), { target: { value: 'Vía nueva' } })
    fireEvent.change(screen.getByLabelText('Descripción ítem 1'), { target: { value: 'Base' } })
    fireEvent.change(screen.getByLabelText('Unidad ítem 1'), { target: { value: 'm3' } })
    fireEvent.change(screen.getByLabelText('Cantidad ítem 1'), { target: { value: '1000' } })
    fireEvent.change(screen.getByLabelText('Valor unitario ítem 1'), { target: { value: '10000' } })
    fireEvent.change(screen.getByLabelText('Administración %'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Imprevistos %'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Utilidad %'), { target: { value: '4' } })
    fireEvent.click(screen.getByText('Crear cotización'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/cotizaciones-obra') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      const body = JSON.parse(call[1].body)
      expect(body.numero).toBeUndefined()   // vacío → autogenerado por el backend
      expect(body.cliente_id).toBe(3)
      expect(body.nombre_obra).toBe('Vía nueva')
      expect(body.administracion_pct).toBe('0.05')   // porcentaje 5 → fracción 0.05
      expect(body.imprevistos_pct).toBe('0.03')
      expect(body.utilidad_pct).toBe('0.04')
      expect(body.iva_sobre_utilidad_pct).toBe('0.19')
      expect(body.items).toEqual([{ orden: 1, descripcion: 'Base', unidad: 'm3', cantidad: '1000', valor_unitario: '10000' }])
    })
  })

  it('convierte una cotización GANADA en obra (POST /cotizaciones-obra/{id}/convertir-obra)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/cotizaciones-obra/7/convertir-obra') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 900, estado: 'PLANIFICADA' }, 200))
      if (u.includes('/cotizaciones-obra/7')) return Promise.resolve(jsonResp(DETALLE_GANADA))
      if (u.includes('/cotizaciones-obra')) return Promise.resolve(jsonResp([{ ...RESUMEN, estado: 'GANADA' }]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Pavimentación vía La Estrella'))   // expande la fila
    fireEvent.click(await screen.findByText('Convertir a obra'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/cotizaciones-obra/7/convertir-obra') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
    })
  })
})
