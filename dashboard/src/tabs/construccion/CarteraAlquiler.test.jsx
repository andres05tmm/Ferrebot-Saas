import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí capturamos el handler (re-fetch) y la lista de eventos.
let rtHandler = null
let rtEventos = null
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: (tipos, handler) => { rtEventos = tipos; rtHandler = handler },
}))
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), message: vi.fn() },
}))

import { toast } from 'sonner'
import CarteraAlquilerSection from './CarteraAlquiler.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

// tope 10M · consumido 3M → 70% disponible = Holgado
// tope 5M · consumido 4.5M → 10% disponible = Al límite (y cliente colita)
// tope 2M · consumido 2.6M → excedido (y cliente colita)
const CUPOS = [
  { id: 10, cliente_id: 1, cliente_nombre: 'Constructora Andes', cupo: '10000000.0000',
    consumido: '3000000.00', disponible: '7000000.0000', vigente_desde: '2026-01-01',
    vigente_hasta: null, activo: true, notas: null },
  { id: 11, cliente_id: 2, cliente_nombre: 'Obras del Valle', cupo: '5000000.0000',
    consumido: '4500000.00', disponible: '500000.0000', vigente_desde: '2026-01-01',
    vigente_hasta: null, activo: true, notas: null },
  { id: 12, cliente_id: 3, cliente_nombre: 'Cimientos SA', cupo: '2000000.0000',
    consumido: '2600000.00', disponible: '-600000.0000', vigente_desde: '2026-01-01',
    vigente_hasta: null, activo: true, notas: null },
]
// dias_sin_abono contra umbral 15: 20 → ámbar (>=15, <30) · 40 → rojo (>=30)
const COLITAS = [
  { cliente_id: 2, cliente_nombre: 'Obras del Valle', obra_id: 100, obra_nombre: 'Puente La Ceja',
    saldo: '4500000.00', dias_sin_abono: 20, ultimo_abono_en: '2026-06-01T12:00:00+00:00' },
  { cliente_id: 3, cliente_nombre: 'Cimientos SA', obra_id: 101, obra_nombre: 'Vía El Retiro',
    saldo: '2600000.00', dias_sin_abono: 40, ultimo_abono_en: null },
]
const CONFIG = { activo: true, dias_colita: 15, cadencia_aviso_dias: 7 }
const CLIENTES = [{ id: 1, nombre: 'Constructora Andes' }, { id: 5, nombre: 'Nueva Cliente SAS' }]
const OBRA_DETALLE = {
  obra_id: 100, obra_nombre: 'Puente La Ceja', cliente_nombre: 'Obras del Valle', saldo: '4500000.00',
  cargos: [{ id: 1, registro_horas_id: 501, maquina_nombre: 'Retro CAT 320', fecha: '2026-06-01',
    horas_facturables: '8', monto: '1440000.00' }],
  abonos: [],
}

function instalarFetch(over = {}) {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/cartera-alquiler/cupos') && opts.method === 'POST') return Promise.resolve(jsonResp({ id: 99 }, 201))
    if (u.includes('/cartera-alquiler/cupos/') && opts.method === 'PUT') return Promise.resolve(jsonResp({ id: 11 }))
    if (u.includes('/cartera-alquiler/cupos')) return Promise.resolve(jsonResp(over.cupos ?? CUPOS))
    if (u.includes('/cartera-alquiler/colitas')) return Promise.resolve(jsonResp(over.colitas ?? COLITAS))
    if (u.includes('/cartera-alquiler/config')) return Promise.resolve(jsonResp(CONFIG))
    if (/\/cartera-alquiler\/obras\//.test(u)) return Promise.resolve(jsonResp(OBRA_DETALLE))
    if (u.includes('/clientes')) return Promise.resolve(jsonResp(CLIENTES))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderCon(features = ['cartera_alquiler']) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <CarteraAlquilerSection />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => { localStorage.clear(); rtHandler = null; rtEventos = null })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('CarteraAlquiler — gate por capacidad', () => {
  it('sin la capacidad cartera_alquiler no pinta nada ni pide datos', () => {
    const fetchMock = instalarFetch()
    renderCon([])
    expect(screen.queryByText('Cartera de alquiler')).toBeNull()
    expect(fetchMock.mock.calls.filter(c => String(c[0]).includes('/cartera-alquiler'))).toHaveLength(0)
  })

  it('con la capacidad activa monta la sección y pide sus datos', async () => {
    const fetchMock = instalarFetch()
    renderCon()
    expect(await screen.findByText('Cartera de alquiler')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/cartera-alquiler/cupos'))).toBe(true)
  })
})

describe('CarteraAlquiler — render con datos', () => {
  it('pinta KPIs y la tabla de cupos con su semáforo de utilización', async () => {
    instalarFetch()
    renderCon()
    await screen.findByText('Constructora Andes')
    // Aparecen en la tabla de cupos y también en la sección de colitas (mismo cliente).
    expect(screen.getAllByText('Obras del Valle').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Cimientos SA').length).toBeGreaterThan(0)
    // KPI "Cupo otorgado" = 10M + 5M + 2M = 17M
    expect(screen.getByText('$17.000.000')).toBeInTheDocument()
    // Semáforos de utilización (color-not-only: llevan etiqueta)
    expect(screen.getByText('Holgado')).toBeInTheDocument()
    expect(screen.getByText('Al límite')).toBeInTheDocument()
    expect(screen.getAllByText('Excedido').length).toBeGreaterThan(0)
  })

  it('expandir una obra estancada carga sus cargos por horas de máquina', async () => {
    instalarFetch()
    renderCon()
    fireEvent.click(await screen.findByText('Puente La Ceja'))
    expect(await screen.findByText('Retro CAT 320')).toBeInTheDocument()
    expect(screen.getByText(/8 h/)).toBeInTheDocument()          // horas facturables
    expect(screen.getByText('$1.440.000')).toBeInTheDocument()    // monto del cargo
  })
})

describe('CarteraAlquiler — semáforo de colita por umbral', () => {
  it('escala el tono con los días sin abono (ámbar al cruzar el umbral, rojo al doblarlo)', async () => {
    instalarFetch()
    renderCon()
    await screen.findByText('Puente La Ceja')

    // 20 d (umbral 15) → ámbar (text-warning); aparece en la fila de cupo del cliente y en la colita.
    const amber = screen.getAllByText(/Colita · 20 d/)
    expect(amber.length).toBeGreaterThan(0)
    expect(amber.some(el => el.className.includes('text-warning'))).toBe(true)

    // 40 d (>= 2×15) → rojo (text-destructive).
    const red = screen.getAllByText(/Colita · 40 d/)
    expect(red.length).toBeGreaterThan(0)
    expect(red.some(el => el.className.includes('text-destructive'))).toBe(true)

    // Solo los clientes/obras en colita muestran chip: 2 clientes colita + 2 obras estancadas = 4.
    // (Constructora Andes NO es colita → no lleva chip.)
    expect(screen.getAllByText(/Colita · \d+ d/)).toHaveLength(4)
  })
})

describe('CarteraAlquiler — estado vacío', () => {
  it('muestra un vacío con propósito cuando no hay cupos ni colitas', async () => {
    instalarFetch({ cupos: [], colitas: [] })
    renderCon()
    expect(await screen.findByText('Sin cupos de alquiler todavía')).toBeInTheDocument()
    expect(screen.getByText('Definir el primer cupo')).toBeInTheDocument()
    expect(screen.getByText('Ninguna obra con cartera estancada')).toBeInTheDocument()
  })
})

describe('CarteraAlquiler — alta de cupo', () => {
  it('crear cupo postea el shape correcto (POST /cartera-alquiler/cupos)', async () => {
    const fetchMock = instalarFetch()
    renderCon()

    fireEvent.click(await screen.findByText('Nuevo cupo'))
    await screen.findByRole('option', { name: 'Nueva Cliente SAS' })   // espera a que carguen los clientes
    fireEvent.change(screen.getByLabelText('Cliente'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Cupo de crédito'), { target: { value: '8000000' } })
    fireEvent.click(screen.getByText('Crear cupo'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/cartera-alquiler/cupos') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(String(call[0])).toBe('/api/v1/cartera-alquiler/cupos')
      const body = JSON.parse(call[1].body)
      expect(body).toMatchObject({ cliente_id: 5, cupo: 8000000, vigente_hasta: null, notas: null })
      expect(body.vigente_desde).toMatch(/^\d{4}-\d{2}-\d{2}$/)   // hoy en hora Colombia
    })
    expect(toast.success).toHaveBeenCalledWith('Cupo creado')
  })
})

describe('CarteraAlquiler — tiempo real', () => {
  it('se suscribe a los eventos de cartera/fiados y el excedido avisa con toast', async () => {
    instalarFetch()
    renderCon()
    await screen.findByText('Constructora Andes')

    expect(rtEventos).toEqual(expect.arrayContaining(
      ['cartera_cupo_excedido', 'cartera_colita', 'fiado_registrado', 'fiado_abonado'],
    ))
    await act(async () => { rtHandler('cartera_cupo_excedido', {}) })
    expect(toast.warning).toHaveBeenCalledWith('Un cliente superó su cupo de alquiler')
  })
})
