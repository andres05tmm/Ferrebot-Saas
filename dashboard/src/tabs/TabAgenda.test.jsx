import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

let rtHandler = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (_tipos, handler) => { rtHandler = handler },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { toast } from 'sonner'
import { FeaturesProvider } from '@/lib/features.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'
import { USER_KEY } from '@/lib/api.js'
import TabAgenda from './TabAgenda.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const CITA = {
  id: 1, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Ana', cliente_telefono: '3001112233',
  inicio: '2026-06-12T10:00:00-05:00', fin: '2026-06-12T10:30:00-05:00', estado: 'pendiente',
  origen: 'whatsapp', notas: null, idempotency_key: null, creada_en: '2026-06-10T10:00:00-05:00',
}

function instalarFetch({ citas } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    const m = opts.method || 'GET'
    calls.push([u, m, opts.body])
    if (/\/agenda\/citas\/\d+\/(confirmar|cancelar|reagendar)/.test(u)) return Promise.resolve(jsonResp({ id: 1, estado: 'confirmada' }, 200))
    if (u.includes('/agenda/citas') && m === 'POST') return Promise.resolve(jsonResp({ id: 99, estado: 'confirmada' }, 201))
    if (u.includes('/agenda/citas')) return Promise.resolve(jsonResp(citas ?? [CITA]))
    if (u.includes('/agenda/servicios') && m === 'POST') return Promise.resolve(jsonResp({ id: 5 }, 201))
    if (/\/agenda\/servicios\/\d+\/recursos/.test(u)) return Promise.resolve(jsonResp([]))
    if (u.includes('/agenda/servicios')) return Promise.resolve(jsonResp([{ id: 1, nombre: 'Limpieza', activo: true, duracion_min: 30, precio: null, buffer_antes_min: 0, buffer_despues_min: 0, categoria: null }]))
    if (u.includes('/agenda/recursos')) return Promise.resolve(jsonResp([{ id: 1, nombre: 'Dra. Pérez', tipo: 'profesional', activo: true }]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

function renderTab() {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={['pack_agenda']}><TabAgenda /></FeaturesProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => { localStorage.clear(); rtHandler = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabAgenda — gating de ruta', () => {
  it('la ruta /agenda está oculta sin pack_agenda y visible con la feature', () => {
    expect(isRouteEnabled('/agenda', [])).toBe(false)
    expect(isRouteEnabled('/agenda', ['pack_agenda'])).toBe(true)
  })
})

// Mañana en hora Colombia (YYYY-MM-DD) — espeja util.sumarDias para verificar la navegación.
function ymdMas(n) {
  const hoy = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
  const d = new Date(`${hoy}T12:00:00-05:00`)
  d.setDate(d.getDate() + n)
  return d.toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

describe('TabAgenda — Calendario', () => {
  it('pinta la grilla agrupada por recurso (columna del profesional + bloque de cita)', async () => {
    instalarFetch()
    renderTab()
    // Encabezado de la columna del recurso y el bloque de la cita dentro de su columna.
    expect(await screen.findByLabelText('Columna Dra. Pérez')).toBeInTheDocument()
    const columna = screen.getByLabelText('Columna Dra. Pérez')
    expect(columna).toBeInTheDocument()
    expect(screen.getAllByText('Ana').length).toBeGreaterThan(0)
  })

  it('tiempo real: un evento de cita recarga grilla y panel', async () => {
    const { calls } = instalarFetch()
    renderTab()
    await screen.findByLabelText('Columna Dra. Pérez')
    const antes = calls.filter(([u, m]) => u.includes('/agenda/citas') && m === 'GET').length

    act(() => { rtHandler?.('cita_agendada', { cita_id: 9 }) })  // como si el agente agendara
    await waitFor(() => {
      const ahora = calls.filter(([u, m]) => u.includes('/agenda/citas') && m === 'GET').length
      expect(ahora).toBeGreaterThan(antes)
    })
  })

  it('navegación de fecha: "día siguiente" consulta el día siguiente', async () => {
    const { calls } = instalarFetch()
    renderTab()
    await screen.findByLabelText('Columna Dra. Pérez')
    const manana = ymdMas(1)

    fireEvent.click(screen.getByLabelText('Día siguiente'))
    await waitFor(() => {
      expect(calls.some(([u]) => u.includes(`/agenda/citas?desde=${manana}&hasta=${manana}`))).toBe(true)
    })
  })

  it('panel Acción Requerida: Aprobar y Rechazar llaman al endpoint correcto', async () => {
    const { calls } = instalarFetch()
    renderTab()
    fireEvent.click(await screen.findByLabelText('Aprobar cita 1'))
    await waitFor(() => {
      expect(calls.some(([u, m]) => u.includes('/agenda/citas/1/confirmar') && m === 'POST')).toBe(true)
    })

    fireEvent.click(screen.getByLabelText('Rechazar cita 1'))
    await waitFor(() => {
      expect(calls.some(([u, m]) => u.includes('/agenda/citas/1/cancelar') && m === 'POST')).toBe(true)
    })
  })

  it('alta manual hace POST /agenda/citas (origen dashboard)', async () => {
    const { calls } = instalarFetch()
    renderTab()
    await screen.findByLabelText('Columna Dra. Pérez')

    fireEvent.click(screen.getByText('Nueva cita'))
    fireEvent.change(screen.getByLabelText('Servicio'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Recurso de la cita'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Fecha y hora'), { target: { value: '2026-06-12T14:00' } })
    fireEvent.change(screen.getByLabelText('Nombre del cliente'), { target: { value: 'Beto' } })
    fireEvent.change(screen.getByLabelText('Teléfono'), { target: { value: '3009998877' } })
    fireEvent.click(screen.getByText('Agendar'))

    await waitFor(() => {
      const call = calls.find(([u, m]) => u.includes('/agenda/citas') && m === 'POST')
      expect(call).toBeTruthy()
      const body = JSON.parse(call[2])
      expect(body).toMatchObject({ servicio_id: 1, recurso_id: 1, cliente_nombre: 'Beto', cliente_telefono: '3009998877' })
      expect(body.inicio).toBe('2026-06-12T14:00:00-05:00')  // sellado a hora Colombia
    })
  })
})

describe('TabAgenda — Configuración (gating admin)', () => {
  it('no-admin ve un aviso, no el CRUD', async () => {
    instalarFetch()
    renderTab()  // sin USER_KEY → no admin
    fireEvent.click(screen.getByText('Configuración'))
    expect(await screen.findByText(/Solo un administrador/i)).toBeInTheDocument()
    expect(screen.queryByText('Nuevo servicio')).toBeNull()
  })

  it('admin crea un servicio (POST /agenda/servicios)', async () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ rol: 'admin' }))
    const { calls } = instalarFetch()
    renderTab()
    fireEvent.click(screen.getByText('Configuración'))

    fireEvent.change(await screen.findByLabelText('Nombre del servicio'), { target: { value: 'Corte' } })
    fireEvent.click(screen.getByText('Crear servicio'))
    await waitFor(() => {
      const call = calls.find(([u, m]) => u.includes('/agenda/servicios') && m === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[2])).toMatchObject({ nombre: 'Corte', duracion_min: 30 })
    })
  })

  it('Reglas: muestra el google_calendar_id del GET y lo incluye en el PUT', async () => {
    localStorage.setItem(USER_KEY, JSON.stringify({ rol: 'admin' }))
    const calls = []
    const fetchMock = vi.fn((url, opts = {}) => {
      const u = String(url); const m = opts.method || 'GET'
      calls.push([u, m, opts.body])
      if (u.includes('/agenda/config') && m === 'PUT') return Promise.resolve(jsonResp({}, 200))
      if (u.includes('/agenda/config')) return Promise.resolve(jsonResp({
        id: 1, zona_horaria: 'America/Bogota', intervalo_slots_min: 15, anticipacion_minima_min: 120,
        ventana_maxima_dias: 30, politica_cancelacion_horas: 24, permite_reagendar: true,
        modo_confirmacion: 'auto', requiere_anticipo: false, anticipo_tipo: null, anticipo_valor: null,
        capacidad_por_slot: 1, recordatorios_horas: [24, 2], persona: null,
        google_calendar_id: 'negocio@group.calendar.google.com',
        creado_en: '2026-06-10T10:00:00-05:00', actualizado_en: null,
      }))
      if (u.includes('/agenda/citas')) return Promise.resolve(jsonResp([CITA]))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderTab()
    fireEvent.click(screen.getByText('Configuración'))
    fireEvent.click(await screen.findByText('Reglas'))

    // GET: el campo muestra el id guardado.
    const input = await screen.findByLabelText('Google Calendar ID')
    expect(input.value).toBe('negocio@group.calendar.google.com')

    // PUT: el nuevo valor viaja en el cuerpo.
    fireEvent.change(input, { target: { value: 'otro@group.calendar.google.com' } })
    fireEvent.click(screen.getByText('Guardar reglas'))
    await waitFor(() => {
      const call = calls.find(([u, m]) => u.includes('/agenda/config') && m === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[2]).google_calendar_id).toBe('otro@group.calendar.google.com')
    })
  })
})
