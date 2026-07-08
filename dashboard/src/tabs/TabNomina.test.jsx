import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabNomina from './TabNomina.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const PERIODO = {
  id: 1, nombre: 'Quincena 1 — julio 2026', tipo: 'QUINCENAL',
  fecha_inicio: '2026-07-01', fecha_fin: '2026-07-15', estado: 'ABIERTO',
  liquidado_en: null, pagado_en: null, creado_en: '2026-07-06T00:00:00-05:00',
}

const PARAMS = {
  smmlv: '1750905', auxilio_transporte: '249095', auxilio_transporte_tope_smmlv: 2, horas_mes: '240',
  recargo_he_diurna: '1.25', recargo_he_nocturna: '1.75', recargo_dominical: '2.0',
  salud_empleado_pct: '0.04', pension_empleado_pct: '0.04', salud_empleador_pct: '0.085',
  pension_empleador_pct: '0.12', arl_pct: '0.0522', caja_compensacion_pct: '0.04', sena_pct: '0.02',
  icbf_pct: '0.03', cesantias_pct: '0.0833', intereses_cesantias_pct: '0.01', prima_pct: '0.0833',
  vacaciones_pct: '0.0417',
}

const DETALLE = {
  id: 10, trabajador_id: 5, trabajador_nombre: 'Ana Ruiz', trabajador_documento: 'CC-1',
  tipo_vinculacion: 'DIRECTO', dias_liquidados: '15', salario_devengado: '750000',
  auxilio_transporte: '124547.50', valor_horas_extra: '0', total_devengado: '874547.50',
  salud_empleado: '30000', pension_empleado: '30000', total_deducciones: '60000',
  neto_pagar: '814547.50', aportes_empleador: '260400', provisiones: '100000',
  costo_total: '1234947.50', cune_dian: null,
}

const DETALLE_PERIODO = {
  ...PERIODO, parametros: PARAMS, detalles: [DETALLE],
  totales: {
    trabajadores: 1, total_devengado: '874547.50', total_deducciones: '60000',
    total_neto: '814547.50', total_aportes: '260400', total_provisiones: '100000',
    total_costo: '1234947.50',
  },
}

const TRABAJADOR = {
  detalle: DETALLE,
  prorrateos: [
    { obra_id: 3, obra_nombre: 'Vía La Paz', dias_imputados: '10', costo_imputado: '800000' },
    { obra_id: null, obra_nombre: null, dias_imputados: '5', costo_imputado: '434947.50' },
  ],
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabNomina /></MemoryRouter>) }

describe('TabNomina', () => {
  it('lista los periodos con su estado como semáforo', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url).includes('/nomina/periodos')) return Promise.resolve(jsonResp([PERIODO]))
      return Promise.resolve(jsonResp([]))
    }))
    render_()
    expect(await screen.findByText('Quincena 1 — julio 2026')).toBeInTheDocument()
    expect(screen.getAllByText('Abierto').length).toBeGreaterThan(0)   // semáforo + chip de filtro
    expect(screen.getByText('Quincenal')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando no hay periodos', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('Todavía no hay periodos de nómina')).toBeInTheDocument()
    expect(screen.getByText('Crear el primer periodo')).toBeInTheDocument()
  })

  it('crear periodo postea el shape correcto (POST /nomina/periodos)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/nomina/periodos') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2 }, 201))
      if (u.includes('/nomina/periodos')) return Promise.resolve(jsonResp([]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nuevo periodo'))
    fireEvent.change(await screen.findByLabelText('Fecha de inicio'), { target: { value: '2026-07-01' } })
    fireEvent.change(await screen.findByLabelText('Fecha fin'), { target: { value: '2026-07-15' } })
    fireEvent.click(screen.getByText('Crear periodo'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/nomina/periodos') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        tipo: 'QUINCENAL', fecha_inicio: '2026-07-01', fecha_fin: '2026-07-15', nombre: null,
      })
    })
  })

  it('al expandir, muestra la liquidación y liquidar pega al endpoint dedicado', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url)
      if (u.includes('/nomina/periodos/1/liquidar') && opts?.method === 'POST') {
        return Promise.resolve(jsonResp({ trabajadores_liquidados: 1, prorrateos: 1, total_costo: '1234947.50' }))
      }
      if (u.includes('/nomina/periodos/1/trabajador/5')) return Promise.resolve(jsonResp(TRABAJADOR))
      if (u.includes('/nomina/periodos/1')) return Promise.resolve(jsonResp(DETALLE_PERIODO))
      if (u.includes('/nomina/periodos')) return Promise.resolve(jsonResp([PERIODO]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Quincena 1 — julio 2026'))   // expande el periodo
    expect(await screen.findByText('Ana Ruiz')).toBeInTheDocument()        // fila de liquidación
    fireEvent.click(screen.getByText('Liquidar'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/nomina/periodos/1/liquidar') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
    })
  })

  it('al expandir un trabajador, muestra el prorrateo por obra (incl. administrativo)', async () => {
    const fetchMock = vi.fn((url) => {
      const u = String(url)
      if (u.includes('/nomina/periodos/1/trabajador/5')) return Promise.resolve(jsonResp(TRABAJADOR))
      if (u.includes('/nomina/periodos/1')) return Promise.resolve(jsonResp(DETALLE_PERIODO))
      if (u.includes('/nomina/periodos')) return Promise.resolve(jsonResp([PERIODO]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Quincena 1 — julio 2026'))   // expande el periodo
    fireEvent.click(await screen.findByText('Ana Ruiz'))                   // expande el trabajador

    expect(await screen.findByText('Prorrateo por obra')).toBeInTheDocument()
    expect(screen.getByText('Vía La Paz')).toBeInTheDocument()
    expect(screen.getByText('Administrativo')).toBeInTheDocument()
  })
})
