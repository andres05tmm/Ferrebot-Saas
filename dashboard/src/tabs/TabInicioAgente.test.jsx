import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, Outlet } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { FeaturesProvider } from '@/lib/features.jsx'
import TabInicioAgente, { construirKpis } from './TabInicioAgente.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const ESCALADAS = [
  { id: 1, cliente_telefono: '573001112233', estado: 'humano', motivo: 'Pide asesor', escalada_en: '2026-06-11T10:00:00-05:00' },
  { id: 2, cliente_telefono: '573004445566', estado: 'humano', motivo: null, escalada_en: '2026-06-11T11:00:00-05:00' },
]
const CITAS = [
  { id: 10, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Ana', inicio: '2026-06-11T15:00:00-05:00', fin: '2026-06-11T15:30:00-05:00', estado: 'confirmada', origen: 'whatsapp' },
  { id: 11, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Beto', inicio: '2026-06-11T09:00:00-05:00', fin: '2026-06-11T09:30:00-05:00', estado: 'pendiente', origen: 'dashboard' },
  { id: 12, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Cancelada', inicio: '2026-06-11T12:00:00-05:00', fin: '2026-06-11T12:30:00-05:00', estado: 'cancelada', origen: 'whatsapp' },
]
const SERVICIOS = [{ id: 1, nombre: 'Limpieza dental', activo: true }]
const REPORTE = {
  desde: '2026-05-12', hasta: '2026-06-11',
  conversaciones: { nuevas: 50, escaladas_a_humano: 10, pct_resueltas_sin_humano: 80 },
  citas: { total: 24, por_estado: { confirmada: 20 }, agendadas_por_agente: 18, reconfirmadas: 12, no_shows: 2 },
  pedidos: { confirmados: 5, entregados: 4, vendido: '320000.00' },
  cotizaciones: { emitidas: 8, aceptadas: 3, conversion_pct: 38, total_aceptado: '900000.00' },
  cobranza: { recordatorios: 14, recuperado: '450000.00' },
  satisfaccion: { promedio: 4.7, respuestas: 9 },
}

function instalarFetch({ escaladas = ESCALADAS, citas = CITAS, reporte = REPORTE } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    calls.push([u, opts.method || 'GET'])
    if (u.includes('/conversaciones/escaladas')) return Promise.resolve(jsonResp(escaladas))
    if (u.includes('/agente/reporte')) return Promise.resolve(jsonResp(reporte))
    if (u.includes('/agenda/servicios')) return Promise.resolve(jsonResp(SERVICIOS))
    if (u.includes('/agenda/citas')) return Promise.resolve(jsonResp(citas))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

// Harness con FeaturesProvider + Outlet (refreshKey), como el shell real.
function renderHome(features) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <Routes>
          <Route element={<Outlet context={{ refreshKey: 0 }} />}>
            <Route index element={<TabInicioAgente />} />
          </Route>
        </Routes>
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

const TODAS = ['pack_agenda', 'canal_whatsapp', 'pack_faq', 'pack_pedidos', 'pack_ventas', 'pack_cobranza', 'pack_postventa']

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('construirKpis — KPIs del reporte del agente', () => {
  it('sin reporte no produce KPIs', () => {
    expect(construirKpis(null)).toEqual([])
  })

  it('solo arma los bloques presentes (un tenant de solo agenda+whatsapp)', () => {
    const kpis = construirKpis({ conversaciones: REPORTE.conversaciones, citas: REPORTE.citas })
    const labels = kpis.map(k => k.label)
    expect(labels).toContain('Resueltas sin humano')
    expect(labels).toContain('Citas')
    expect(labels).not.toContain('Recuperado')        // sin pack_cobranza
    expect(labels).not.toContain('Satisfacción')      // sin pack_postventa
  })

  it('recorta a un máximo de 7 tarjetas con todos los packs', () => {
    const kpis = construirKpis(REPORTE)
    expect(kpis.length).toBe(7)
  })
})

describe('TabInicioAgente — render por features', () => {
  it('tenant completo: banner de pendientes, KPIs, próximas citas y acciones', async () => {
    instalarFetch()
    renderHome(TODAS)

    // Pendientes de asesor (conteo de escaladas).
    expect(await screen.findByText('2 clientes esperando asesor')).toBeInTheDocument()
    // KPI del reporte.
    expect(await screen.findByText('Resueltas sin humano')).toBeInTheDocument()
    expect(screen.getByText('80%')).toBeInTheDocument()
    // Próximas citas (ordenadas por hora; excluye la cancelada).
    expect(screen.getByText('Ana')).toBeInTheDocument()
    expect(screen.getByText('Beto')).toBeInTheDocument()
    expect(screen.queryByText('Cancelada')).toBeNull()
    expect(screen.getAllByText('Limpieza dental').length).toBeGreaterThan(0)
    // Acciones rápidas de servicio (la CTA del banner también dice "Abrir inbox").
    expect(screen.getAllByText('Abrir inbox').length).toBeGreaterThan(0)
    expect(screen.getByText('Ver agenda')).toBeInTheDocument()
  })

  it('solo canal_whatsapp: muestra banner+KPIs, NO pide agenda', async () => {
    const { calls } = instalarFetch()
    renderHome(['canal_whatsapp'])

    expect(await screen.findByText('2 clientes esperando asesor')).toBeInTheDocument()
    expect(screen.queryByText('Próximas citas de hoy')).toBeNull()
    // No se piden endpoints de agenda (no tiene el pack).
    expect(calls.some(([u]) => u.includes('/agenda/citas'))).toBe(false)
    expect(calls.some(([u]) => u.includes('/agente/reporte'))).toBe(true)
  })

  it('solo pack_agenda (sin whatsapp): muestra citas, NO banner ni reporte', async () => {
    const { calls } = instalarFetch()
    renderHome(['pack_agenda'])

    expect(await screen.findByText('Próximas citas de hoy')).toBeInTheDocument()
    expect(screen.getByText('Ana')).toBeInTheDocument()
    // Sin canal_whatsapp no hay banner de pendientes ni se pide el reporte (daría 403).
    expect(screen.queryByText(/esperando asesor/)).toBeNull()
    expect(calls.some(([u]) => u.includes('/conversaciones/escaladas'))).toBe(false)
    expect(calls.some(([u]) => u.includes('/agente/reporte'))).toBe(false)
  })

  it('tiempo real: un evento de escalada refetchea', async () => {
    const { calls } = instalarFetch()
    renderHome(TODAS)
    await screen.findByText('2 clientes esperando asesor')
    const antes = calls.filter(([u]) => u.includes('/conversaciones/escaladas')).length

    act(() => { rtHandler?.('conversacion_escalada', { conversacion_id: 9 }) })
    await waitFor(() => {
      const ahora = calls.filter(([u]) => u.includes('/conversaciones/escaladas')).length
      expect(ahora).toBeGreaterThan(antes)
    })
  })
})
