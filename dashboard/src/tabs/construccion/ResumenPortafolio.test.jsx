import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import ResumenPortafolio from './ResumenPortafolio.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const PANEL = {
  total_obras: 3, obras_activas: 2,
  por_estado: { EN_EJECUCION: 1, PLANIFICADA: 1, LIQUIDADA: 1 },
  ingreso_presupuestado_total: '10000000.00',
  gasto_total: '4000000.00',
  utilidad_real_total: '6000000.00',
  obras_en_alerta: 1,
  obras: [
    { obra_id: 1, nombre: 'Vía La Estrella', estado: 'EN_EJECUCION', ingreso_presupuestado: '10000000.00',
      gasto_total: '4000000.00', utilidad_real: '6000000.00', tiene_presupuesto: true, semaforo: 'rojo',
      alerta_margen: true },
  ],
}

function instalarFetch(panel = PANEL) {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/obras/panel')) return Promise.resolve(jsonResp(panel))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('ResumenPortafolio — home de obra (Fase 8)', () => {
  it('pide /obras/panel y pinta el rollup + conteo por estado + alertas', async () => {
    const fetchMock = instalarFetch()
    render(<ResumenPortafolio refreshKey={0} />)
    expect(await screen.findByText('Portafolio de obras')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/obras/panel'))).toBe(true)
    // KPIs (COP, sin float)
    expect(screen.getByText('$10.000.000')).toBeInTheDocument()   // presupuestado
    expect(screen.getByText('$4.000.000')).toBeInTheDocument()    // gasto real
    expect(screen.getByText('$6.000.000')).toBeInTheDocument()    // utilidad real
    // Badge de obras en alerta + fila de la obra en pérdida
    expect(screen.getByText('1 en alerta')).toBeInTheDocument()
    expect(screen.getByText('Vía La Estrella')).toBeInTheDocument()
    expect(screen.getByText('En pérdida')).toBeInTheDocument()
  })

  it('no pinta nada cuando el portafolio está vacío (0 obras)', async () => {
    instalarFetch({ ...PANEL, total_obras: 0, obras: [] })
    const { container } = render(<ResumenPortafolio refreshKey={0} />)
    // Da tiempo al fetch; el componente devuelve null con total_obras=0.
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByText('Portafolio de obras')).toBeNull()
    expect(container).toBeEmptyDOMElement()
  })
})
