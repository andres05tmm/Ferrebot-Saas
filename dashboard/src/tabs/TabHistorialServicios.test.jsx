import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { FeaturesProvider } from '@/lib/features.jsx'
import { hoyCO, masDiasCO, sumarDias } from './agenda/util.jsx'
import TabHistorialServicios from './TabHistorialServicios.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }
const at = (ymd, hhmm) => `${ymd}T${hhmm}:00-05:00`
const HOY = hoyCO()

// ── Fixtures por vertical (relativas a hoy: el filtro de rango es client-side sobre fechas reales) ──
// Restaurante: el endpoint debería devolver solo finales, pero metemos un no-final y uno fuera de rango
// para verificar el refuerzo client-side (estado final + fecha en rango).
const PEDIDOS = [
  { id: 1, cliente_nombre: 'Ana', cliente_telefono: '3001', estado: 'entregado', total: '39000.00',
    origen: 'whatsapp', creado_en: at(HOY, '10:00'),
    items: [{ id: 1, nombre: 'Hamburguesa', cantidad: '2', subtotal: '36000.00' }] },
  { id: 2, cliente_nombre: 'Beto', cliente_telefono: '3002', estado: 'cancelado', total: '25000.00',
    origen: 'dashboard', creado_en: at(masDiasCO(-3), '12:00'),
    items: [{ id: 2, nombre: 'Pizza', cantidad: '1', subtotal: '20000.00' }] },
  { id: 3, cliente_nombre: 'Viejo', cliente_telefono: '3003', estado: 'entregado', total: '10000.00',
    origen: 'whatsapp', creado_en: at(masDiasCO(-60), '10:00'), items: [] },          // fuera de rango
  { id: 4, cliente_nombre: 'EnCamino', cliente_telefono: '3004', estado: 'en_camino', total: '5000.00',
    origen: 'whatsapp', creado_en: at(HOY, '11:00'), items: [] },                      // no final
]

// Barbería/clínica: citas pasadas en estado final. La `confirmada` (no final) no debe aparecer.
const SERVICIOS = [{ id: 1, nombre: 'Corte', activo: true }]
const CITAS = [
  { id: 10, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Ana', inicio: at(masDiasCO(-1), '09:00'), fin: at(masDiasCO(-1), '09:30'), estado: 'cumplida', origen: 'whatsapp' },
  { id: 11, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Beto', inicio: at(masDiasCO(-2), '10:00'), fin: at(masDiasCO(-2), '10:30'), estado: 'cancelada', origen: 'dashboard' },
  { id: 12, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Caro', inicio: at(masDiasCO(-3), '11:00'), fin: at(masDiasCO(-3), '11:30'), estado: 'no_show', origen: 'whatsapp' },
  { id: 13, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Dani', inicio: at(HOY, '15:00'), fin: at(HOY, '15:30'), estado: 'confirmada', origen: 'whatsapp' },
]

// Hotel: reservas (citas sobre `habitacion`) con check-out pasado o canceladas.
const RECURSOS = [
  { id: 1, nombre: 'Suite 101', tipo: 'habitacion', activo: true },
  { id: 2, nombre: 'Habitación 102', tipo: 'habitacion', activo: true },
  { id: 3, nombre: 'Recepción', tipo: 'profesional', activo: true },
]
const RESERVAS = [
  { id: 20, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Huesped Uno', inicio: at(masDiasCO(-4), '15:00'), fin: at(masDiasCO(-2), '12:00'), estado: 'confirmada', origen: 'whatsapp' },
  { id: 21, servicio_id: 1, recurso_id: 2, cliente_nombre: 'Huesped Dos', inicio: at(masDiasCO(-3), '15:00'), fin: at(masDiasCO(-1), '12:00'), estado: 'cancelada', origen: 'dashboard' },
  { id: 22, servicio_id: 1, recurso_id: 1, cliente_nombre: 'Futuro', inicio: at(masDiasCO(2), '15:00'), fin: at(masDiasCO(5), '12:00'), estado: 'confirmada', origen: 'whatsapp' },   // check-out futuro
  { id: 23, servicio_id: 1, recurso_id: 3, cliente_nombre: 'NoHab', inicio: at(masDiasCO(-4), '15:00'), fin: at(masDiasCO(-2), '12:00'), estado: 'confirmada', origen: 'whatsapp' },   // no es habitación
]

function instalarFetch({ pedidos = PEDIDOS, citas = CITAS, recursos = RECURSOS } = {}) {
  const calls = []
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    calls.push(u)
    if (u.includes('/agenda/servicios')) return Promise.resolve(jsonResp(SERVICIOS))
    if (u.includes('/agenda/recursos')) return Promise.resolve(jsonResp(recursos))
    if (u.includes('/agenda/citas')) return Promise.resolve(jsonResp(citas))
    if (u.includes('/pedidos')) return Promise.resolve(jsonResp(pedidos))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

function renderHistorial(features) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <TabHistorialServicios />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

const RESTAURANTE = ['pos', 'pack_pedidos', 'canal_whatsapp']
const BARBERIA = ['pack_agenda', 'canal_whatsapp']
const HOTEL = ['pack_agenda', 'pack_reservas', 'canal_whatsapp']

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabHistorialServicios — restaurante (pedidos)', () => {
  it('lista pedidos finales en rango; excluye no-finales y fuera de rango', async () => {
    instalarFetch()
    renderHistorial(RESTAURANTE)

    expect(await screen.findByText('Historial de pedidos')).toBeInTheDocument()
    expect(screen.getByText('Ana')).toBeInTheDocument()       // entregado, hoy
    expect(screen.getByText('Beto')).toBeInTheDocument()      // cancelado, -3d
    expect(screen.queryByText('Viejo')).toBeNull()            // fuera de rango (-60d)
    expect(screen.queryByText('EnCamino')).toBeNull()         // no final
    expect(screen.getByText('2 resultados')).toBeInTheDocument()
  })

  it('pide el endpoint filtrando por estados finales', async () => {
    const { calls } = instalarFetch()
    renderHistorial(RESTAURANTE)
    await screen.findByText('Ana')
    expect(calls.some((u) => u.includes('/pedidos?estado=entregado&estado=cancelado'))).toBe(true)
  })

  it('el chip de estado filtra (Cancelados deja solo el cancelado)', async () => {
    instalarFetch()
    renderHistorial(RESTAURANTE)
    await screen.findByText('Ana')

    fireEvent.click(screen.getByRole('button', { name: 'Cancelados' }))
    expect(screen.queryByText('Ana')).toBeNull()
    expect(screen.getByText('Beto')).toBeInTheDocument()
    expect(screen.getByText('1 resultado')).toBeInTheDocument()
  })

  it('expandir una fila muestra el detalle de ítems', async () => {
    instalarFetch()
    renderHistorial(RESTAURANTE)
    await screen.findByText('Ana')

    fireEvent.click(screen.getByRole('button', { name: 'Pedido 1' }))
    expect(await screen.findByText('2× Hamburguesa')).toBeInTheDocument()
  })

  it('empty state propio cuando no hay pedidos', async () => {
    instalarFetch({ pedidos: [] })
    renderHistorial(RESTAURANTE)
    expect(await screen.findByText('Sin pedidos en el rango.')).toBeInTheDocument()
  })
})

describe('TabHistorialServicios — barbería (citas)', () => {
  it('lista citas pasadas en estado final, con servicio; excluye no-finales', async () => {
    instalarFetch()
    renderHistorial(BARBERIA)

    expect(await screen.findByText('Historial de citas')).toBeInTheDocument()
    expect(screen.getByText('Ana')).toBeInTheDocument()    // cumplida
    expect(screen.getByText('Beto')).toBeInTheDocument()   // cancelada
    expect(screen.getByText('Caro')).toBeInTheDocument()   // no_show
    expect(screen.queryByText('Dani')).toBeNull()          // confirmada (no final)
    expect(screen.getAllByText('Corte').length).toBeGreaterThan(0)
    expect(screen.getByText('3 resultados')).toBeInTheDocument()
  })

  it('el chip No asistió deja solo el no_show', async () => {
    instalarFetch()
    renderHistorial(BARBERIA)
    await screen.findByText('Ana')

    fireEvent.click(screen.getByRole('button', { name: 'No asistió' }))
    expect(screen.getByText('Caro')).toBeInTheDocument()
    expect(screen.queryByText('Ana')).toBeNull()
    expect(screen.queryByText('Beto')).toBeNull()
  })
})

describe('TabHistorialServicios — hotel (reservas)', () => {
  it('lista reservas con check-out pasado o canceladas, sobre habitaciones', async () => {
    instalarFetch({ citas: RESERVAS })
    renderHistorial(HOTEL)

    expect(await screen.findByText('Historial de reservas')).toBeInTheDocument()
    expect(screen.getByText('Huesped Uno')).toBeInTheDocument()   // check-out pasado
    expect(screen.getByText('Huesped Dos')).toBeInTheDocument()   // cancelada
    expect(screen.queryByText('Futuro')).toBeNull()               // check-out futuro
    expect(screen.queryByText('NoHab')).toBeNull()                // recurso no-habitación
    expect(screen.getByText('Suite 101')).toBeInTheDocument()
    expect(screen.getByText('Habitación 102')).toBeInTheDocument()
    expect(screen.getByText('2 resultados')).toBeInTheDocument()
  })

  it('pide /agenda/citas en ventana amplia hacia atrás + /agenda/recursos', async () => {
    const { calls } = instalarFetch({ citas: RESERVAS })
    renderHistorial(HOTEL)
    await screen.findByText('Huesped Uno')

    expect(calls.some((u) => u.includes('/agenda/recursos'))).toBe(true)
    const citasCall = calls.find((u) => u.includes('/agenda/citas'))
    expect(citasCall).toContain(`desde=${sumarDias(masDiasCO(-30), -30)}`)
    expect(citasCall).toContain(`hasta=${HOY}`)
  })

  it('el chip Canceladas deja solo la reserva cancelada', async () => {
    instalarFetch({ citas: RESERVAS })
    renderHistorial(HOTEL)
    await screen.findByText('Huesped Uno')

    fireEvent.click(screen.getByRole('button', { name: 'Canceladas' }))
    expect(screen.getByText('Huesped Dos')).toBeInTheDocument()
    expect(screen.queryByText('Huesped Uno')).toBeNull()
  })

  it('empty state propio cuando no hay reservas', async () => {
    instalarFetch({ citas: [] })
    renderHistorial(HOTEL)
    expect(await screen.findByText('Sin reservas en el rango.')).toBeInTheDocument()
  })
})
