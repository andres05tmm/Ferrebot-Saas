import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabResbalos from './TabResbalos.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const RESBALOS = [
  { id: 1, proveedor_nombre: 'Planta Asfalto', categoria: 'MEZCLA_ASFALTICA', total: '1000000.00',
    precio_venta_cliente: '1150000.00', resbalo: '150000.0000', resbalo_pct: '13.04', resbalo_alerta: false },
  { id: 2, proveedor_nombre: 'Planta Ajustada', categoria: 'MEZCLA_ASFALTICA', total: '1000000.00',
    precio_venta_cliente: '1020000.00', resbalo: '20000.0000', resbalo_pct: '1.96', resbalo_alerta: true },
]
const ANALISIS = [
  { proveedor_id: 1, proveedor_nombre: 'Planta Única', categoria: 'MEZCLA_ASFALTICA', n_compras: 2,
    cantidad_total: '101', costo_unitario_promedio: '100.99', costo_unitario_min: '100.00',
    costo_unitario_max: '200.00', variacion_pct: '98.04', alerta: true },
]

function instalarFetch(over = {}) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/compras/resbalos')) return Promise.resolve(jsonResp(over.resbalos ?? RESBALOS))
    if (u.includes('/compras/analisis-precios')) return Promise.resolve(jsonResp(over.analisis ?? ANALISIS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderTab() {
  return render(<MemoryRouter><TabResbalos /></MemoryRouter>)
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabResbalos — reporte de resbalos + análisis de precios (Fase 8)', () => {
  it('pide ambos endpoints y pinta las dos secciones con sus datos', async () => {
    const fetchMock = instalarFetch()
    renderTab()
    expect(await screen.findByText('Reporte de resbalos')).toBeInTheDocument()
    expect(screen.getByText('Análisis de precios de proveedor')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/compras/resbalos'))).toBe(true)
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/compras/analisis-precios'))).toBe(true)

    // Resbalos: proveedor + alerta de baja rentabilidad en la fila del margen chico.
    expect(await screen.findByText('Planta Asfalto')).toBeInTheDocument()
    expect(screen.getByText('Baja rentabilidad')).toBeInTheDocument()

    // Análisis: proveedor + señal de sobreprecio (máximo > 15% sobre el promedio ponderado).
    expect(await screen.findByText('Planta Única')).toBeInTheDocument()
    expect(screen.getByText('Sobreprecio')).toBeInTheDocument()
  })

  it('muestra estados vacíos con propósito cuando no hay datos', async () => {
    instalarFetch({ resbalos: [], analisis: [] })
    renderTab()
    expect(await screen.findByText('Sin viajes de material en el período')).toBeInTheDocument()
    expect(screen.getByText('Sin compras en el período')).toBeInTheDocument()
  })
})
