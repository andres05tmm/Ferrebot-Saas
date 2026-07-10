/*
 * CalendarioObra.test.jsx — página /calendario (Commit 3 PIM). Trabaja contra el CONTRATO JSON pactado
 * (el backend aún no existe): se stubbea `fetch` ramificado por URL. Cubre la grilla con dots, el detalle
 * del día, el conmutador de vista, el día "solo planeado" y la navegación de mes.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

// El stream lo controla RealtimeProvider; aquí no queremos abrir SSE ni suscribir de verdad.
vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import CalendarioObra from './CalendarioObra.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

// Mes/día en hora Colombia = el que pide el componente (hoyCO). Construimos las fechas del payload sobre
// el mes en curso para que caigan dentro de la grilla renderizada.
const YMD = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
const YM = YMD.slice(0, 7)
const MES_NUM = Number(YM.slice(5, 7))
const MES_SIG = MES_NUM === 12 ? 1 : MES_NUM + 1
const F_ACT = `${YM}-09`   // día con actividad real
const F_PLAN = `${YM}-15`  // día SOLO planeado (asignación sin actividad)

const MES = {
  anio: Number(YM.slice(0, 4)),
  mes: MES_NUM,
  dias: [
    {
      fecha: F_ACT,
      horas_maquina_total: '12.5',
      conteos: {
        horas_maquina: 2, reportes: 1, asistencias: 3, mantenimientos: 0,
        consumos: 1, hitos: 0, proximos_mantenimientos: 1,
        maquinas_asignadas: 2, trabajadores_asignados: 3,
      },
    },
    {
      fecha: F_PLAN,
      horas_maquina_total: '0',
      conteos: {
        horas_maquina: 0, reportes: 0, asistencias: 0, mantenimientos: 0,
        consumos: 0, hitos: 0, proximos_mantenimientos: 0,
        maquinas_asignadas: 1, trabajadores_asignados: 0,
      },
    },
  ],
}

const DIA = {
  fecha: F_ACT,
  horas_maquina: [{
    id: 1, maquina_id: 1, maquina: 'Retroexcavadora CAT 416', obra_id: 2, obra: 'Via Llanogrande',
    operador_id: 3, operador: 'Juan Perez', horas_trabajadas: '8.0', horas_facturables: '8.0',
    observaciones: null, origen_registro: 'MANUAL',
  }],
  reportes: [{
    id: 1, obra_id: 2, obra: 'Via Llanogrande', reportado_por: 'Ana Ruiz',
    avance_descripcion: 'Base compactada', m2_ejecutados: '120.0', m3_ejecutados: null, incidentes: null, foto_urls: [],
  }],
  asistencia: [{
    id: 1, trabajador_id: 5, trabajador: 'Pedro Gomez', obra_id: 2, obra: 'Via Llanogrande',
    horas_trabajadas: '8', horas_extra_diurnas: '0', horas_extra_nocturnas: '0', horas_dominical_festivo: '0', ausencia: null,
  }],
  mantenimientos: [],
  consumos: [{ id: 1, obra_id: 2, obra: 'Via Llanogrande', producto_id: 5, producto: 'Cemento', cantidad: '10' }],
  hitos: [],
  proximos_mantenimientos: [{ maquina_id: 1, maquina: 'Retroexcavadora CAT 416', tipo: 'PREVENTIVO', descripcion: 'Cambio de aceite' }],
  planeado_maquinas: [{
    asignacion_id: 1, maquina_id: 1, maquina: 'Retroexcavadora CAT 416', obra_id: 2, obra: 'Via Llanogrande',
    operador_id: 3, operador: 'Juan Perez', fecha_inicio: `${YM}-01`, fecha_fin: null,
  }],
  planeado_trabajadores: [{
    asignacion_id: 1, trabajador_id: 5, trabajador: 'Pedro Gomez', obra_id: 2, obra: 'Via Llanogrande',
    fecha_inicio: `${YM}-01`, fecha_fin: null,
  }],
}

// Orden de ramas: `/obras/calendario/dia` antes de `/obras/calendario` antes de `/obras`.
function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/obras/calendario/dia')) return Promise.resolve(jsonResp(DIA))
    if (u.includes('/obras/calendario')) return Promise.resolve(jsonResp(MES))
    if (u.includes('/maquinas')) return Promise.resolve(jsonResp([]))
    if (u.includes('/trabajadores')) return Promise.resolve(jsonResp([]))
    if (u.includes('/obras')) return Promise.resolve(jsonResp([]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderCal() {
  return render(<MemoryRouter><CalendarioObra /></MemoryRouter>)
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'admin' }))
  if (!window.matchMedia) {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }))
  }
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('CalendarioObra — calendario de obra (/calendario)', () => {
  it('la grilla pide el mes y pinta dots en el día con actividad', async () => {
    instalarFetch()
    renderCal()
    // El día con actividad expone sus conteos en el aria-label/title (2 horas máquina…).
    const celda = await screen.findByRole('button', { name: /2 horas máquina/ })
    // Cinco tipos con conteo>0 → 4 dots + "+1" (mapeo de dots documentado).
    expect(celda.querySelectorAll('.rounded-full').length).toBeGreaterThanOrEqual(4)
    expect(within(celda).getByText('+1')).toBeInTheDocument()
  })

  it('clic en un día abre el detalle y renderiza las secciones con nombres del payload', async () => {
    const fetchMock = instalarFetch()
    renderCal()
    fireEvent.click(await screen.findByRole('button', { name: /2 horas máquina/ }))

    // La máquina aparece en Máquinas, Mantenimientos (próximo) y Planeado → getAllByText.
    expect((await screen.findAllByText('Retroexcavadora CAT 416')).length).toBeGreaterThan(0)
    expect(screen.getByText('Base compactada')).toBeInTheDocument()   // reporte de obra
    expect(screen.getAllByText('Pedro Gomez').length).toBeGreaterThan(0)  // asistencia + planeado
    expect(screen.getByText('Cemento')).toBeInTheDocument()           // consumo de material
    // Se pidió el detalle del día por su fecha.
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes(`/obras/calendario/dia?fecha=${F_ACT}`))).toBe(true)
  })

  it('el conmutador de vista (Trabajadores) oculta las secciones de máquinas y muestra su select', async () => {
    instalarFetch()
    renderCal()
    fireEvent.click(await screen.findByRole('button', { name: /2 horas máquina/ }))
    expect((await screen.findAllByText('Retroexcavadora CAT 416')).length).toBeGreaterThan(0)

    const grupo = screen.getByRole('group', { name: 'Ver calendario por' })
    fireEvent.click(within(grupo).getByRole('button', { name: 'Trabajadores' }))

    // Máquinas (y sus mantenimientos/planeado) fuera; la asistencia (Pedro Gomez) sigue; aparece el select.
    expect(screen.queryByText('Retroexcavadora CAT 416')).toBeNull()
    expect(screen.getAllByText('Pedro Gomez').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Trabajador')).toBeInTheDocument()
  })

  it('distingue el día SOLO planeado con borde punteado', async () => {
    instalarFetch()
    renderCal()
    const celda = await screen.findByRole('button', { name: new RegExp(`${F_PLAN}: solo planeado`) })
    expect(celda.className).toContain('border-dashed')
  })

  it('la navegación de mes cambia el query del fetch mensual', async () => {
    const fetchMock = instalarFetch()
    renderCal()
    await screen.findByRole('button', { name: /2 horas máquina/ })

    fireEvent.click(screen.getByRole('button', { name: 'Mes siguiente' }))
    // Lookahead: `mes=1` no debe colar por `mes=11`/`mes=12` (el mes va al final del query).
    const reMes = new RegExp(`mes=${MES_SIG}(?!\\d)`)
    expect(fetchMock.mock.calls.some((c) => reMes.test(String(c[0])))).toBe(true)
  })
})
