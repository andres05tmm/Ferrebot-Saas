/*
 * FormAsignacionMaquina.test.jsx — Commit 4 "Calendario de obra PIM": formularios de asignación.
 * Trabaja contra el CRUD real del backend (contrato del commit 4) con `fetch` stubbeado por URL/método.
 * Cubre: el payload que arma el POST (campos vacíos NO viajan), el 409 con su `detail`, y la visibilidad
 * por rol de los botones de asignación en DetalleDia (el vendedor no los ve; el admin sí y el form abre).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))
import { toast } from 'sonner'

import FormAsignacionMaquina from './FormAsignacionMaquina.jsx'
import DetalleDia from './DetalleDia.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const OBRAS = [{ id: 5, nombre: 'Vía Llanogrande' }, { id: 6, nombre: 'Puente Norte' }]
const TRABAJADORES = [{ id: 7, nombres: 'Juan', apellidos: 'Pérez' }]
const MAQUINA = { id: 3, codigo: 'M-003', nombre: 'Retroexcavadora CAT 420' }

// Instala fetch: POST de asignación configurable (default 201) + GETs de catálogos.
function instalarFetch({ postResp = jsonResp({ id: 1 }, 201) } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/asignaciones') && opts?.method === 'POST') return Promise.resolve(postResp)
    if (u.includes('/obras')) return Promise.resolve(jsonResp(OBRAS))
    if (u.includes('/trabajadores')) return Promise.resolve(jsonResp(TRABAJADORES))
    if (u.includes('/maquinas')) return Promise.resolve(jsonResp([MAQUINA]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Cuerpo del POST de asignación (parseado) de un fetchMock.
function payloadDe(fetchMock) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/asignaciones') && c[1]?.method === 'POST')
  return call ? JSON.parse(call[1].body) : null
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('FormAsignacionMaquina — POST de asignación', () => {
  it('arma el payload con obra_id y fecha_inicio; los campos vacíos NO viajan', async () => {
    const fetchMock = instalarFetch()
    render(<FormAsignacionMaquina maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Asignar máquina/i }))

    await waitFor(() => expect(payloadDe(fetchMock)).not.toBeNull())
    // Se pegó al endpoint de la máquina FIJA.
    expect(fetchMock.mock.calls.some((c) =>
      String(c[0]).includes('/maquinas/3/asignaciones') && c[1]?.method === 'POST')).toBe(true)
    const body = payloadDe(fetchMock)
    expect(body.obra_id).toBe(5)
    expect(body.fecha_inicio).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    // Los opcionales vacíos NO se envían (el backend aplica sus defaults).
    expect(body).not.toHaveProperty('precio_hora')
    expect(body).not.toHaveProperty('minimo_horas')
    expect(body).not.toHaveProperty('fecha_fin')
    expect(body).not.toHaveProperty('operador_id')
  })

  it('envía precio_hora y operador_id cuando se llenan', async () => {
    const fetchMock = instalarFetch()
    render(<FormAsignacionMaquina maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('Operador'), { target: { value: '7' } })
    fireEvent.change(screen.getByLabelText('Precio / hora'), { target: { value: '130000' } })
    fireEvent.click(screen.getByRole('button', { name: /Asignar máquina/i }))

    await waitFor(() => expect(payloadDe(fetchMock)).not.toBeNull())
    const body = payloadDe(fetchMock)
    expect(body).toMatchObject({ obra_id: 6, operador_id: 7, precio_hora: 130000 })
  })

  it('sin obra no dispara el POST (validación cliente)', async () => {
    const fetchMock = instalarFetch()
    render(<FormAsignacionMaquina maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Asignar máquina/i }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(payloadDe(fetchMock)).toBeNull()
  })

  it('muestra el detail del backend ante un 409 (solape)', async () => {
    const detalle = 'La máquina ya tiene una asignación activa que solapa'
    instalarFetch({ postResp: jsonResp({ detail: detalle }, 409) })
    const onExito = vi.fn()
    render(<FormAsignacionMaquina maquinaFija={MAQUINA} onExito={onExito} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Asignar máquina/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detalle))
    expect(onExito).not.toHaveBeenCalled()
  })
})

// ── Visibilidad por rol en DetalleDia ──────────────────────────────────────────────────────────────
// Fecha FUTURA fija (patrón DetalleDia.test.jsx): en días pasados los botones "+ Asignar" se ocultan
// a propósito, así que una fecha "de hoy" hace el test dependiente del reloj.
const F = '2999-12-15'
const DIA = {
  fecha: F,
  horas_maquina: [], reportes: [], asistencia: [], mantenimientos: [], consumos: [],
  hitos: [], proximos_mantenimientos: [], planeado_trabajadores: [],
  planeado_maquinas: [{
    asignacion_id: 1, maquina_id: 3, maquina: 'Retroexcavadora CAT 420', obra_id: 5, obra: 'Vía Llanogrande',
    operador_id: 7, operador: 'Juan Pérez', fecha_inicio: '2026-07-01', fecha_fin: null,
  }],
}

function instalarFetchDia() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/obras/calendario/dia')) return Promise.resolve(jsonResp(DIA))
    if (u.includes('/obras')) return Promise.resolve(jsonResp(OBRAS))
    if (u.includes('/trabajadores')) return Promise.resolve(jsonResp(TRABAJADORES))
    if (u.includes('/maquinas')) return Promise.resolve(jsonResp([MAQUINA]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const FILTROS = { vista: 'todos', obraId: '', maquinaId: '', trabajadorId: '' }

describe('DetalleDia — asignación en Planeado por rol', () => {
  it('el vendedor NO ve los botones de asignación ni Cerrar', async () => {
    localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'vendedor' }))
    instalarFetchDia()
    render(<DetalleDia fecha={F} filtros={FILTROS} onCerrar={vi.fn()} onCambio={vi.fn()} />)
    // El planeado se lista (nombre de la máquina) pero sin acciones de admin.
    expect(await screen.findByText('Retroexcavadora CAT 420')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Asignar máquina/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Asignar trabajador/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Cerrar$/ })).toBeNull()
  })

  it('el admin ve los botones y el form de asignar máquina se despliega', async () => {
    localStorage.setItem('ferrebot_user', JSON.stringify({ rol: 'admin' }))
    instalarFetchDia()
    render(<DetalleDia fecha={F} filtros={FILTROS} onCerrar={vi.fn()} onCambio={vi.fn()} />)

    const abrir = await screen.findByRole('button', { name: /Asignar máquina/i })
    expect(screen.getByRole('button', { name: /Asignar trabajador/i })).toBeInTheDocument()
    fireEvent.click(abrir)
    // Abierto el form: aparece su campo de Precio / hora (exclusivo del form, no del listado).
    expect(await screen.findByLabelText('Precio / hora')).toBeInTheDocument()
  })
})
