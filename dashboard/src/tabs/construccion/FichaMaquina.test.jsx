import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import FichaMaquina from './FichaMaquina.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const MAQUINA = {
  id: 3, codigo: 'M-003', nombre: 'Retroexcavadora CAT 420', tipo: 'Retroexcavadora',
  placa: 'ABC123', serial: 'SN-9', anio_fabricacion: 2019, estado: 'OCUPADA',
  precio_hora_default: '120000.00', minimo_horas_factura: 4, costo_operacion_hora: '40000.00',
}

// Asignación vigente que cubre el parte de horas → el ingreso se calcula con el precio PACTADO (130k),
// no con el default de la máquina (120k).
const ASIGNACIONES = [
  { id: 10, maquina_id: 3, obra_id: 5, fecha_inicio: '2026-06-01', fecha_fin: null,
    precio_hora: '130000.00', minimo_horas: 4, operador_id: null, activa: true },
]
const HORAS = [
  { id: 100, maquina_id: 3, obra_id: 5, fecha: '2026-06-10', horas_trabajadas: '8.00',
    horas_facturables: '8.00', operador_id: null, observaciones: null,
    origen_registro: 'TELEGRAM_BOT', creado_en: '2026-06-10T20:00:00+00:00' },
]
// proximo_en_fecha muy pasada (2026-06-01) vs hoy (jul 2026) → badge "Vencido".
const MANTS = [
  { id: 200, maquina_id: 3, tipo: 'PREVENTIVO', fecha: '2026-05-01', horas_maquina: '100.00',
    descripcion: 'Cambio de aceite y filtros', costo: '250000.00', proveedor_id: null,
    proximo_en_horas: null, proximo_en_fecha: '2026-06-01', factura_url: null,
    creado_en: '2026-05-01T12:00:00+00:00' },
]

function instalarFetch({ asignaciones = ASIGNACIONES, horas = HORAS, mants = MANTS } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/mantenimientos') && opts?.method === 'POST') {
      return Promise.resolve(jsonResp({ id: 201, maquina_id: 3, tipo: 'CORRECTIVO', fecha: '2026-07-08',
        horas_maquina: null, descripcion: 'x', costo: '0', proveedor_id: null, proximo_en_horas: null,
        proximo_en_fecha: null, factura_url: null, creado_en: '2026-07-08T12:00:00+00:00' }, 201))
    }
    if (u.includes('/asignaciones')) return Promise.resolve(jsonResp(asignaciones))
    if (u.includes('/horas')) return Promise.resolve(jsonResp(horas))
    if (u.includes('/mantenimientos')) return Promise.resolve(jsonResp(mants))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('FichaMaquina — ficha rica de la máquina (F4)', () => {
  it('pinta asignaciones, kárdex con ingreso por parte y el badge de mantenimiento vencido', async () => {
    instalarFetch()
    render(<FichaMaquina id="ficha" maquina={MAQUINA} isAdmin obrasNombre={{ 5: 'Vía La Estrella' }} />)

    // Asignación (nombre de obra resuelto por el mapa) + chip Activa. El nombre aparece tanto en la
    // tabla de asignaciones como en el kárdex, así que hay ≥1 coincidencia.
    expect((await screen.findAllByText('Vía La Estrella')).length).toBeGreaterThan(0)
    expect(screen.getByText('Activa')).toBeInTheDocument()

    // Kárdex: ingreso por parte = 8 h × $130.000 = $1.040.000 (precio pactado, no el default).
    expect(screen.getByText('$1.040.000')).toBeInTheDocument()
    // Chip de origen del bot.
    expect(screen.getByText('bot')).toBeInTheDocument()

    // Mantenimiento vencido (proximo_en_fecha en el pasado).
    expect(screen.getByText('Cambio de aceite y filtros')).toBeInTheDocument()
    expect(screen.getByText('Vencido')).toBeInTheDocument()
  })

  it('oculta el total facturado del kárdex al vendedor (no admin)', async () => {
    instalarFetch()
    render(<FichaMaquina id="ficha" maquina={MAQUINA} isAdmin={false} obrasNombre={{ 5: 'Vía La Estrella' }} />)
    await screen.findAllByText('Vía La Estrella')
    expect(screen.queryByText(/Total facturado/)).toBeNull()
    // El costo interno/hora tampoco se filtra al vendedor.
    expect(screen.queryByText('Costo interno / hora')).toBeNull()
  })

  it('muestra el total facturado del kárdex al admin', async () => {
    instalarFetch()
    render(<FichaMaquina id="ficha" maquina={MAQUINA} isAdmin obrasNombre={{ 5: 'Vía La Estrella' }} />)
    expect(await screen.findByText(/Total facturado/)).toBeInTheDocument()
  })

  it('el formulario de mantenimiento hace POST al endpoint (admin)', async () => {
    const fetchMock = instalarFetch()
    render(<FichaMaquina id="ficha" maquina={MAQUINA} isAdmin obrasNombre={{}} />)
    await screen.findByText('Cambio de aceite y filtros')

    // Abrir el form (progressive disclosure: colapsado por defecto).
    fireEvent.click(screen.getByRole('button', { name: /Registrar/i }))
    const desc = await screen.findByLabelText('Descripción')
    fireEvent.change(desc, { target: { value: 'Reparación de bomba hidráulica' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar mantenimiento/i }))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) =>
        String(c[0]).includes('/maquinas/3/mantenimientos') && c[1]?.method === 'POST')).toBe(true)
    })
  })

  it('no oculta la ficha si una sección falla (kárdex vacío degrada a su copy)', async () => {
    // horas devuelve [] → el kárdex muestra su vacío, pero asignaciones y mantenimientos siguen.
    instalarFetch({ horas: [] })
    render(<FichaMaquina id="ficha" maquina={MAQUINA} isAdmin obrasNombre={{ 5: 'Vía La Estrella' }} />)
    expect(await screen.findByText('Vía La Estrella')).toBeInTheDocument()
    expect(screen.getByText(/Sin partes de horas/)).toBeInTheDocument()
  })
})
