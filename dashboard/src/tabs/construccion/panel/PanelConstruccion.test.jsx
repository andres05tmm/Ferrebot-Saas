import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí no queremos abrir SSE ni que el feed se suscriba de verdad.
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import PanelConstruccion from './PanelConstruccion.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const DASHBOARD = {
  generado_en: '2026-07-08T12:00:00-05:00',
  mes: { desde: '2026-07-01', hasta: '2026-07-31' },
  kpis_mes: {
    ingreso_alquiler: '600000.00', resbalos: '50000.00', ingreso_total: '650000.00',
    gastos: '230000.00', compras: '150000.00', gasto_total: '380000.00',
    utilidad_estimada: '270000.00', margen_pct: '41.54',
    semaforo_utilidad: 'verde', flujo_caja_neto: '270000.00',
    mes_anterior: { ingreso_total: '500000.00', gasto_total: '400000.00' },
  },
  portafolio: {
    total_obras: 2, obras_activas: 2, por_estado: { EN_EJECUCION: 2 },
    ingreso_presupuestado_total: '3000000.00', gasto_total: '2200000.00',
    utilidad_real_total: '800000.00', obras_en_alerta: 1,
    // Rojas primero: el backend ya las ordena por riesgo; el componente respeta el orden del payload.
    obras: [
      { obra_id: 1, nombre: 'Vía La Ceja', estado: 'EN_EJECUCION', cliente_id: 2, cliente_nombre: 'Alcaldía',
        ingreso_presupuestado: '1000000.00', gasto_total: '1200000.00', utilidad_real: '-200000.00',
        tiene_presupuesto: true, semaforo: 'rojo', alerta_margen: true },
      { obra_id: 2, nombre: 'Bodega Norte', estado: 'EN_EJECUCION', cliente_id: 3, cliente_nombre: 'Constructora Sur',
        ingreso_presupuestado: '2000000.00', gasto_total: '1000000.00', utilidad_real: '1000000.00',
        tiene_presupuesto: true, semaforo: 'verde', alerta_margen: false },
    ],
  },
  maquinas: {
    total: 2, por_estado: { OCUPADA: 1, DISPONIBLE: 1 },
    ocupadas_hoy: [{ maquina_id: 1, maquina: 'Vibro CAT', obra_nombre: 'Vía La Ceja',
      operador_nombre: 'Juan Pérez', horas_hoy: '6', ingreso_hoy: '600000.00' }],
    top_mes: [{ maquina_id: 1, maquina: 'Vibro CAT', horas: '6', ingreso: '600000.00' }],
  },
  alertas: [
    { tipo: 'mantenimiento_vencido', severidad: 'rojo', titulo: 'Mantenimiento vencido: Vibro CAT',
      detalle: 'programado para 2026-07-01', ref_id: 1, ruta: '/maquinas' },
  ],
  conteos: { gastos_por_revisar: 1, colitas: 0, cotizaciones_por_vencer: 1 },
}

function instalarFetch(payload = DASHBOARD, { pendiente = false } = {}) {
  const fetchMock = vi.fn((url) => {
    if (String(url).includes('/obras/dashboard')) {
      return pendiente ? new Promise(() => {}) : Promise.resolve(jsonResp(payload))
    }
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'admin' })) }
function comoVendedor() { localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'vendedor' })) }

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/panel']}>
      <Routes>
        <Route path="/panel" element={<PanelConstruccion />} />
        <Route path="/obras" element={<div>PAGINA OBRAS</div>} />
        <Route path="/maquinas" element={<div>PAGINA MAQUINAS</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  // framer-motion (MotionConfig reducedMotion="user") consulta matchMedia; jsdom no lo trae.
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }))
  }
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('PanelConstruccion — cockpit del dueño (F3)', () => {
  it('admin: pide /obras/dashboard y pinta los KPIs del mes con formato COP', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    renderPanel()

    expect(await screen.findByText('$650.000')).toBeInTheDocument()   // ingreso del mes
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/obras/dashboard'))).toBe(true)
    expect(screen.getByText('$380.000')).toBeInTheDocument()          // gastos del mes
    // F2.4: la utilidad aparece UNA vez — murió la tesela "Flujo de caja" (alias del mismo número).
    expect(screen.getAllByText('$270.000')).toHaveLength(1)
    expect(screen.queryByText('Flujo de caja')).toBeNull()
    // El semáforo de margen (verde) lleva etiqueta (color-not-only).
    expect(screen.getByText('Saludable')).toBeInTheDocument()
  })

  it('respeta el orden por riesgo del payload: la obra en pérdida va primero', async () => {
    comoAdmin()
    instalarFetch()
    renderPanel()

    const tabla = await screen.findByRole('table')
    const filas = within(tabla).getAllByRole('row')
    // filas[0] = encabezado; la primera fila de datos es la roja (Vía La Ceja).
    expect(filas[1]).toHaveTextContent('Vía La Ceja')
    expect(within(filas[1]).getByText('En pérdida')).toBeInTheDocument()
    expect(filas[2]).toHaveTextContent('Bodega Norte')
    expect(within(filas[2]).getByText('Rentable')).toBeInTheDocument()
  })

  it('una alerta navega a su ruta al hacer clic', async () => {
    comoAdmin()
    instalarFetch()
    renderPanel()

    const enlace = await screen.findByRole('link', { name: /Mantenimiento vencido/ })
    fireEvent.click(enlace)
    expect(await screen.findByText('PAGINA MAQUINAS')).toBeInTheDocument()
  })

  it('el vendedor NO ve el cockpit: se le redirige a /obras y no se pide el endpoint', async () => {
    comoVendedor()
    const fetchMock = instalarFetch()
    renderPanel()

    expect(await screen.findByText('PAGINA OBRAS')).toBeInTheDocument()
    expect(screen.queryByText('$650.000')).toBeNull()
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('/obras/dashboard'))).toBe(false)
  })

  it('muestra el esqueleto mientras carga (sin KPIs todavía)', async () => {
    comoAdmin()
    instalarFetch(DASHBOARD, { pendiente: true })
    const { container } = renderPanel()

    await waitFor(() => expect(container.querySelector('.animate-pulse')).not.toBeNull())
    expect(screen.queryByText('$650.000')).toBeNull()
  })

  it('estado vacío cuando no hay obras (con CTA a crear obra)', async () => {
    comoAdmin()
    instalarFetch({ ...DASHBOARD, portafolio: { ...DASHBOARD.portafolio, total_obras: 0, obras: [] } })
    renderPanel()

    expect(await screen.findByText('El panel cobra vida con tu primera obra')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Ir a Obras/ })).toBeInTheDocument()
    expect(screen.queryByText('$650.000')).toBeNull()   // sin KPIs en el vacío
  })
})
