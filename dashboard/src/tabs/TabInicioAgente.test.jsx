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
import { hoyCO, masDiasCO } from './agenda/util.jsx'
import TabInicioAgente, { construirKpis } from './TabInicioAgente.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

// Fixtures de reservas RELATIVAS a hoy (el bloque hotelero filtra client-side por la fecha real).
const at = (ymd, hhmm) => `${ymd}T${hhmm}:00-05:00`
const HOY = hoyCO()

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
// Hotel: recursos `habitacion` (+ uno no-habitación para verificar el filtro) y reservas de hoy.
const RECURSOS = [
  { id: 1, nombre: 'Suite 101', tipo: 'habitacion', activo: true },
  { id: 2, nombre: 'Habitación 102', tipo: 'habitacion', activo: true },
  { id: 3, nombre: 'Recepción', tipo: 'profesional', activo: true },
]
const RESERVAS = [
  { id: 20, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Llega Hoy', inicio: at(HOY, '15:00'), fin: at(masDiasCO(2), '12:00'), estado: 'confirmada', origen: 'whatsapp' },
  { id: 21, servicio_id: 1, recurso_id: 2, cliente_nombre: 'Sale Hoy', inicio: at(masDiasCO(-2), '15:00'), fin: at(HOY, '12:00'), estado: 'confirmada', origen: 'dashboard' },
  { id: 22, servicio_id: 1, recurso_id: 1, cliente_nombre: 'En Casa', inicio: at(masDiasCO(-1), '15:00'), fin: at(masDiasCO(3), '12:00'), estado: 'confirmada', origen: 'whatsapp' },
  { id: 23, servicio_id: 1, recurso_id: 2, cliente_nombre: 'Reserva Cancelada', inicio: at(HOY, '16:00'), fin: at(masDiasCO(2), '12:00'), estado: 'cancelada', origen: 'whatsapp' },
]
const HOTEL = ['pack_agenda', 'pack_reservas', 'pack_faq', 'canal_whatsapp']
const BARBERIA = ['pack_agenda', 'canal_whatsapp']
const REPORTE = {
  desde: '2026-05-12', hasta: '2026-06-11',
  conversaciones: { nuevas: 50, escaladas_a_humano: 10, pct_resueltas_sin_humano: 80 },
  citas: { total: 24, por_estado: { confirmada: 20 }, agendadas_por_agente: 18, reconfirmadas: 12, no_shows: 2 },
  pedidos: { confirmados: 5, entregados: 4, vendido: '320000.00' },
  cotizaciones: { emitidas: 8, aceptadas: 3, conversion_pct: 38, total_aceptado: '900000.00' },
  cobranza: { recordatorios: 14, recuperado: '450000.00' },
  satisfaccion: { promedio: 4.7, respuestas: 9 },
}

function instalarFetch({ escaladas = ESCALADAS, citas = CITAS, reporte = REPORTE, recursos = RECURSOS } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    calls.push([u, opts.method || 'GET'])
    if (u.includes('/conversaciones/escaladas')) return Promise.resolve(jsonResp(escaladas))
    if (u.includes('/agente/reporte')) return Promise.resolve(jsonResp(reporte))
    if (u.includes('/agenda/servicios')) return Promise.resolve(jsonResp(SERVICIOS))
    if (u.includes('/agenda/recursos')) return Promise.resolve(jsonResp(recursos))
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
    // Próximas citas (ordenadas por hora; excluye la cancelada). findBy: el fetch de citas puede
    // resolver después que el del reporte (carrera en CI).
    expect(await screen.findByText('Ana')).toBeInTheDocument()
    expect(screen.getByText('Beto')).toBeInTheDocument()
    expect(screen.queryByText('Cancelada')).toBeNull()
    expect((await screen.findAllByText('Limpieza dental')).length).toBeGreaterThan(0)  // /agenda/servicios es otro fetch
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
    // findBy: el header renderiza durante el loading; las citas llegan con el fetch (carrera en CI).
    expect(await screen.findByText('Ana')).toBeInTheDocument()
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

describe('TabInicioAgente — home de reservas (HOTEL, contenido por vertical)', () => {
  it('hotel (pack_reservas): muestra Llegan/Salen/En casa con huésped y habitación, NO el bloque de citas', async () => {
    instalarFetch({ citas: RESERVAS })
    renderHome(HOTEL)

    expect(await screen.findByText('Reservas de hoy')).toBeInTheDocument()
    // Las tres subsecciones hoteleras (lenguaje correcto, no "cita"). findBy en la primera:
    // el header renderiza durante el loading y las reservas llegan con el fetch (carrera en CI).
    expect(await screen.findByText('Llegan')).toBeInTheDocument()
    expect(screen.getByText('Salen')).toBeInTheDocument()
    expect(screen.getByText('En casa')).toBeInTheDocument()
    // Huéspedes por movimiento; la reserva cancelada se excluye.
    expect(screen.getByText('Llega Hoy')).toBeInTheDocument()
    expect(screen.getByText('Sale Hoy')).toBeInTheDocument()
    expect(screen.getByText('En Casa')).toBeInTheDocument()
    expect(screen.queryByText('Reserva Cancelada')).toBeNull()
    // Nombre de habitación resuelto vía /agenda/recursos (fetch APARTE del de citas → findAll).
    expect((await screen.findAllByText('Suite 101')).length).toBeGreaterThan(0)
    expect(screen.getByText('Habitación 102')).toBeInTheDocument()
    // NO el bloque de citas de servicio.
    expect(screen.queryByText('Próximas citas de hoy')).toBeNull()
  })

  it('hotel: pide recursos + citas en VENTANA AMPLIA (no desde=hoy&hasta=hoy)', async () => {
    const { calls } = instalarFetch({ citas: RESERVAS })
    renderHome(HOTEL)
    await screen.findByText('Reservas de hoy')

    expect(calls.some(([u]) => u.includes('/agenda/recursos'))).toBe(true)
    const citasCall = calls.find(([u]) => u.includes('/agenda/citas'))
    expect(citasCall).toBeTruthy()
    expect(citasCall[0]).toContain(`desde=${masDiasCO(-30)}`)
    expect(citasCall[0]).toContain(`hasta=${masDiasCO(30)}`)
  })

  it('hotel sin movimientos hoy: ofrece próximas llegadas en vez de un vacío seco', async () => {
    const FUTURAS = [
      { id: 30, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Futuro Huésped', inicio: at(masDiasCO(1), '15:00'), fin: at(masDiasCO(3), '12:00'), estado: 'confirmada', origen: 'whatsapp' },
    ]
    instalarFetch({ citas: FUTURAS })
    renderHome(HOTEL)

    expect(await screen.findByText('Próximas llegadas')).toBeInTheDocument()
    expect(await screen.findByText('Futuro Huésped')).toBeInTheDocument()
    expect(screen.queryByText('No hay reservas próximas.')).toBeNull()
  })

  it('hotel sin reservas: muestra el vacío propio sin romper el resto del home', async () => {
    instalarFetch({ citas: [] })
    renderHome(HOTEL)

    expect(await screen.findByText('No hay reservas próximas.')).toBeInTheDocument()
    // El resto del home sigue (acciones rápidas).
    expect(screen.getByText('Ver agenda')).toBeInTheDocument()
  })

  it('barbería (pack_agenda sin reservas): mantiene el bloque de citas, sin reservas ni recursos', async () => {
    const { calls } = instalarFetch()
    renderHome(BARBERIA)

    expect(await screen.findByText('Próximas citas de hoy')).toBeInTheDocument()
    expect(await screen.findByText('Ana')).toBeInTheDocument()
    expect(screen.queryByText('Reservas de hoy')).toBeNull()
    // No pide recursos (no es hotel) ni usa la ventana amplia.
    expect(calls.some(([u]) => u.includes('/agenda/recursos'))).toBe(false)
  })

  it('tiempo real: un evento de cita refetchea las reservas (una reserva ES una cita)', async () => {
    const { calls } = instalarFetch({ citas: RESERVAS })
    renderHome(HOTEL)
    await screen.findByText('Reservas de hoy')
    const antes = calls.filter(([u]) => u.includes('/agenda/citas')).length

    act(() => { rtHandler?.('cita_agendada', { cita_id: 99 }) })
    await waitFor(() => {
      const ahora = calls.filter(([u]) => u.includes('/agenda/citas')).length
      expect(ahora).toBeGreaterThan(antes)
    })
  })
})
