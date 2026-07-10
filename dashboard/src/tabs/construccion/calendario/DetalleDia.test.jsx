/*
 * DetalleDia.test.jsx — el detalle del día del calendario de obra, rediseñado tras el feedback PIM.
 *
 * Se prueba el encabezado CONTEXTUAL del bloque planeado (día pasado ≠ día futuro), el fraseo humano de
 * las asignaciones (nunca "→ —") y los números sin ceros colgantes. Fechas fijas lejanas (2020 / 2999)
 * para que la relación pasado/futuro no dependa del día en que corra la suite. Fetch stubbeado por URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import DetalleDia from './DetalleDia.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const PAST = '2020-01-15'   // pasado seguro
const FUTURE = '2999-12-15' // futuro seguro

// Un día con: horas de máquina (decimal crudo del backend), una asignación de máquina ABIERTA (sin
// fecha_fin) con operador, y una asignación de trabajador CERRADA (con fecha_fin) para probar "hasta".
const DIA = {
  horas_maquina: [{
    id: 1, maquina_id: 5, maquina: 'Minicargador', obra_id: 7, obra: 'Via Llanogrande',
    operador_id: 1, operador: 'Juan Perez', horas_trabajadas: '6.0000', horas_facturables: '6.0000',
    observaciones: null, origen_registro: 'MANUAL',
  }],
  reportes: [], consumos: [], asistencia: [], mantenimientos: [], hitos: [], proximos_mantenimientos: [],
  planeado_maquinas: [{
    asignacion_id: 1, maquina_id: 5, maquina: 'Minicargador', obra_id: 7, obra: 'Via Llanogrande',
    operador_id: 1, operador: 'Juan Perez', fecha_inicio: '2020-01-01', fecha_fin: null,
  }],
  planeado_trabajadores: [{
    asignacion_id: 1, trabajador_id: 1, trabajador: 'Pedro Gomez', obra_id: 7, obra: 'Via Llanogrande',
    fecha_inicio: '2020-01-01', fecha_fin: '2020-02-20',
  }],
}

function instalarFetch() {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp(DIA))))
}

function renderDetalle(fecha) {
  return render(
    <MemoryRouter>
      <DetalleDia fecha={fecha} filtros={{ vista: 'todos' }} onCerrar={() => {}} onCambio={() => {}} />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'admin' }))
  instalarFetch()
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('DetalleDia — detalle del día (rediseño PIM)', () => {
  it('un día PASADO titula "En obra ese día", no muestra "→ —" ni botones de Asignar', async () => {
    const { container } = renderDetalle(PAST)
    expect(await screen.findByText('En obra ese día')).toBeInTheDocument()
    // Fraseo humano de la asignación abierta: "desde el 1 ene 2020", jamás el "→ —" críptico ni el ISO.
    expect(screen.getAllByText(/desde el 1 ene 2020/).length).toBeGreaterThan(0)
    expect(container.textContent).not.toContain('→ —')
    expect(container.textContent).not.toContain('2020-01-01')
    // Una asignación cerrada muestra "hasta el …".
    expect(screen.getByText(/hasta el 20 feb 2020/)).toBeInTheDocument()
    // El pasado no se planea: sin botones "+ Asignar" (pero podría haber "Cerrar", que sí se conserva).
    expect(screen.queryByRole('button', { name: /Asignar/ })).toBeNull()
  })

  it('un día FUTURO titula "Planeado" y sí ofrece los botones de Asignar (admin)', async () => {
    renderDetalle(FUTURE)
    expect(await screen.findByText('Planeado')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Asignar máquina/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Asignar trabajador/ })).toBeInTheDocument()
    expect(screen.getAllByText(/desde el 1 ene 2020/).length).toBeGreaterThan(0)
  })

  it('las horas de máquina se muestran humanas ("6 h"), sin los ceros colgantes del backend', async () => {
    const { container } = renderDetalle(PAST)
    await screen.findAllByText('Minicargador')
    expect(screen.getByText(/·\s*6 h/)).toBeInTheDocument()
    expect(container.textContent).not.toContain('6.0000')
  })

  it('un parte SIN turnos cae al display legacy: "operador X" de cabecera, sin sublíneas de franja', async () => {
    renderDetalle(PAST)
    await screen.findAllByText('Minicargador')
    // Exacto: la meta de la máquina dice justo "operador Juan Perez" (el planeado lo lleva dentro de una
    // frase más larga "…· operador Juan Perez", que NO iguala este texto).
    expect(screen.getByText('operador Juan Perez')).toBeInTheDocument()
    expect(screen.queryByText(/8:00.*13:00/)).toBeNull()
  })

  it('ofrece "Registrar horas" en la sección Máquinas (captura de campo, todos los roles)', async () => {
    renderDetalle(PAST)
    await screen.findAllByText('Minicargador')
    expect(screen.getByRole('button', { name: /Registrar horas/i })).toBeInTheDocument()
  })
})

// ── Desglose de rotación de operadores (turnos) ─────────────────────────────────────────────────────
// Un parte con >1 turno muestra el TOTAL del día en la línea y, debajo, una sublínea por turno
// (operador · franja · horas). Fecha lejana para no depender del día de la suite.
const DIA_TURNOS = {
  ...DIA,
  horas_maquina: [{
    id: 2, maquina_id: 5, maquina: 'Minicargador', obra_id: 7, obra: 'Via Llanogrande',
    operador_id: null, operador: null, horas_trabajadas: '8.0000', horas_facturables: '8.0000',
    observaciones: null, origen_registro: 'MANUAL',
    turnos: [
      { id: 10, operador_id: 1, operador: 'Juan Perez', hora_inicio: '08:00', hora_fin: '13:00', horas: '5.0000' },
      { id: 11, operador_id: 2, operador: 'Pedro Gomez', hora_inicio: '14:00', hora_fin: '17:00', horas: '3.0000' },
    ],
  }],
  planeado_maquinas: [], planeado_trabajadores: [],
}

describe('DetalleDia — rotación de operadores (turnos)', () => {
  it('renderiza una sublínea por turno con operador y franja; el total del día en la cabecera', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp(DIA_TURNOS))))
    renderDetalle(PAST)
    await screen.findAllByText('Minicargador')
    // Total del día en la línea del parte.
    expect(screen.getByText(/·\s*8 h/)).toBeInTheDocument()
    // Una sublínea por turno: operador + franja humana (cero inicial recortado) + horas.
    expect(screen.getByText('Juan Perez')).toBeInTheDocument()
    expect(screen.getByText('Pedro Gomez')).toBeInTheDocument()
    expect(screen.getByText(/8:00.*13:00/)).toBeInTheDocument()
    expect(screen.getByText(/14:00.*17:00/)).toBeInTheDocument()
    // Sin "operador X" de cabecera cuando ya hay turnos (no se duplica el operador).
    expect(screen.queryByText(/operador Juan Perez/)).toBeNull()
  })
})
