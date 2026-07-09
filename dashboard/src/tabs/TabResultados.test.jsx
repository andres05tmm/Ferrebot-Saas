import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabResultados from './TabResultados.jsx'

const RESULTADOS = {
  desde: '2026-06-01', hasta: '2026-06-05',
  ingresos: '100000.00', costo_ventas: '60000.00', utilidad_bruta: '40000.00',
  gastos: '15000.00', utilidad_neta: '25000.00',
}
const FLUJO = {
  desde: '2026-06-01', hasta: '2026-06-05',
  ventas_por_metodo: { efectivo: '20000.00' }, ventas_fiado: '10000.00',
  abonos_fiados: '4000.00', ingresos_caja: '0', total_entradas: '24000.00',
  gastos_por_categoria: { otros: '5000.00' }, abonos_proveedores: '3000.00',
  egresos_caja: '0', total_salidas: '8000.00', neto: '16000.00',
}
const MARGEN = [{
  clave: 'Martillo', producto_id: 1, cantidad: '2.000', ingresos: '20000.00',
  cogs: '24000.00', margen: '-4000.00', margen_pct: '-20.00', cobertura_pct: '50.00',
}]

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/reportes/resultados')) return Promise.resolve(jsonResp(RESULTADOS))
    if (u.includes('/reportes/flujo-dinero')) return Promise.resolve(jsonResp(FLUJO))
    if (u.includes('/reportes/margen-productos')) return Promise.resolve(jsonResp(MARGEN))
    if (u.includes('/reportes/proyeccion-caja')) return Promise.resolve(jsonResp({
      dias_restantes: 10, promedio_venta_diaria: '10000.00', promedio_gasto_diario: '2000.00',
      ventas_mes_actual: '50000.00', gastos_mes_actual: '10000.00',
      proyeccion_ventas_mes: '150000.00', proyeccion_gastos_mes: '30000.00',
      proyeccion_neto_mes: '120000.00',
    }))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabResultados', () => {
  it('admin: pide /reportes/resultados (rango del mes) y pinta el P&L', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText('$100.000')).toBeInTheDocument()   // ingresos
    expect(screen.getByText('$60.000')).toBeInTheDocument()           // costo de ventas
    expect(screen.getByText('$25.000')).toBeInTheDocument()           // utilidad neta
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/reportes/resultados'))
    expect(String(call[0])).toContain('desde=')
    expect(String(call[0])).toContain('hasta=')
  })

  it('sub-tab Flujo: entradas sin el fiado (que viaja como nota de cartera) y el neto', async () => {
    const { fireEvent } = await import('@testing-library/react')
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    await screen.findByText('$100.000')

    fireEvent.click(screen.getByRole('button', { name: 'Flujo de dinero' }))
    expect(await screen.findByText('$24.000')).toBeInTheDocument()      // total entradas
    expect(screen.getByText('$16.000')).toBeInTheDocument()             // neto
    expect(screen.getByText(/eso es cartera/)).toBeInTheDocument()      // fiado explicado
    expect(screen.getByText('Abonos a proveedores')).toBeInTheDocument()
  })

  it('sub-tab Margen: pinta el margen y marca la cobertura de costo incompleta', async () => {
    const { fireEvent } = await import('@testing-library/react')
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    await screen.findByText('$100.000')

    fireEvent.click(screen.getByRole('button', { name: 'Margen por producto' }))
    expect(await screen.findByText('Martillo')).toBeInTheDocument()
    expect(screen.getByText('$-4.000')).toBeInTheDocument()
    expect(screen.getByText(/costo incompleto/)).toBeInTheDocument()    // margen no confiable, avisado
  })

  it('vendedor: NO ve el P&L ni pide el endpoint', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/reportes/resultados'))).toBe(false)
  })
})
