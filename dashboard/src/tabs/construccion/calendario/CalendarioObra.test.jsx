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
// El borde punteado "solo planeado" ahora solo aplica a HOY/futuro: usamos HOY como día SOLO-planeado
// (siempre >= hoy) y otro día distinto para la actividad real (su fecha no influye en esos tests).
const HOY_DIA = Number(YMD.slice(8, 10))
const F_PLAN = YMD // día SOLO planeado = hoy (garantiza borde punteado sin depender del calendario)
const F_ACT = `${YM}-${String(HOY_DIA === 9 ? 8 : 9).padStart(2, '0')}` // día con actividad real, ≠ hoy

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

// Estado actual (franja sobre la grilla): una máquina OCUPADA con obra/operador/horas del mes y otra
// DISPONIBLE sin obra; un trabajador en obra con máquina. horas_mes viene como decimal string crudo.
const ESTADO = {
  fecha: YMD,
  maquinas: [
    {
      maquina_id: 5, maquina: 'Minicargador', estado: 'OCUPADA', obra_id: 7, obra: 'Via Llanogrande K2+300',
      operador_id: 1, operador: 'Juan Perez', desde: `${YM}-01`, horas_mes: '6.0000',
    },
    {
      maquina_id: 6, maquina: 'Retroexcavadora', estado: 'DISPONIBLE', obra_id: null, obra: null,
      operador_id: null, operador: null, desde: null, horas_mes: '0',
    },
  ],
  trabajadores: [
    {
      trabajador_id: 1, trabajador: 'Juan Perez', obra_id: 7, obra: 'Via Llanogrande K2+300',
      desde: `${YM}-01`, maquina_id: 5, maquina: 'Minicargador',
    },
  ],
}

// Orden de ramas: `/obras/calendario/estado` y `/obras/calendario/dia` antes de `/obras/calendario` (que
// los contiene como prefijo) antes de `/obras`.
function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/obras/calendario/estado')) return Promise.resolve(jsonResp(ESTADO))
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

  it('la franja Estado actual muestra dónde está cada máquina y sus horas del mes (sin ceros crudos)', async () => {
    instalarFetch()
    renderCal()
    // La máquina OCUPADA con su obra (aparece también en el bloque de trabajadores → getAllByText).
    expect((await screen.findAllByText('Minicargador')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('Via Llanogrande K2+300').length).toBeGreaterThan(0)
    // Horas del mes en formato humano: "6 h este mes", nunca el decimal crudo "6.0000".
    expect(screen.getByText(/6 h este mes/)).toBeInTheDocument()
    expect(screen.queryByText(/6\.0000/)).toBeNull()
    // La máquina DISPONIBLE aparece con sus horas del mes en 0 (también sin ceros colgantes).
    expect(screen.getByText('Retroexcavadora')).toBeInTheDocument()
    expect(screen.getByText(/0 h este mes/)).toBeInTheDocument()
  })

  it('en vista Máquinas la franja Estado actual oculta el bloque de trabajadores', async () => {
    instalarFetch()
    renderCal()
    await screen.findAllByText('Minicargador')
    const grupo = screen.getByRole('group', { name: 'Ver calendario por' })
    fireEvent.click(within(grupo).getByRole('button', { name: 'Máquinas' }))
    // Solo el bloque de máquinas: Minicargador queda (una vez), el trabajador Juan Perez ya no.
    expect(screen.getAllByText('Minicargador').length).toBeGreaterThan(0)
    expect(screen.queryByText('Juan Perez')).toBeNull()
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
