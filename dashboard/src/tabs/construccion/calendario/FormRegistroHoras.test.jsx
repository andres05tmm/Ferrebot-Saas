/*
 * FormRegistroHoras.test.jsx — registro de un parte/turno de horas de máquina (rotación de operadores).
 * Trabaja contra el contrato POST /maquinas/{id}/horas con `fetch` stubbeado por URL/método. Cubre: el
 * payload que arma (campos vacíos NO viajan; hora_inicio/fin cuando se llenan), el toast con el total del
 * DÍA que devuelve el backend, el aviso de replay (idempotencia, no duplica) y el 409 con su `detail`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))
import { toast } from 'sonner'

import FormRegistroHoras from './FormRegistroHoras.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const OBRAS = [{ id: 5, nombre: 'Vía Llanogrande' }, { id: 6, nombre: 'Puente Norte' }]
const TRABAJADORES = [{ id: 7, nombres: 'Juan', apellidos: 'Pérez' }]
const MAQUINA = { id: 3, codigo: 'M-003', nombre: 'Retroexcavadora CAT 420' }

// Instala fetch: POST de horas configurable (default 201 con total del día) + GETs de catálogos.
function instalarFetch({ postResp = jsonResp({ turnos: [], horas_trabajadas: '8.0000', horas_facturables: '8.0000', ingreso: 0 }, 201) } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/horas') && opts?.method === 'POST') return Promise.resolve(postResp)
    if (u.includes('/obras')) return Promise.resolve(jsonResp(OBRAS))
    if (u.includes('/trabajadores')) return Promise.resolve(jsonResp(TRABAJADORES))
    if (u.includes('/maquinas')) return Promise.resolve(jsonResp([MAQUINA]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

// Cuerpo del POST de horas (parseado) de un fetchMock.
function payloadDe(fetchMock) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/horas') && c[1]?.method === 'POST')
  return call ? JSON.parse(call[1].body) : null
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('FormRegistroHoras — POST de horas/turno', () => {
  it('arma el payload con obra_id, horas_trabajadas y fecha; los opcionales vacíos NO viajan', async () => {
    const fetchMock = instalarFetch()
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))

    await waitFor(() => expect(payloadDe(fetchMock)).not.toBeNull())
    // Se pegó al endpoint de la máquina FIJA.
    expect(fetchMock.mock.calls.some((c) =>
      String(c[0]).includes('/maquinas/3/horas') && c[1]?.method === 'POST')).toBe(true)
    const body = payloadDe(fetchMock)
    expect(body.obra_id).toBe(5)
    expect(body.horas_trabajadas).toBe(5)
    expect(body.fecha).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    // Los opcionales vacíos NO se envían.
    expect(body).not.toHaveProperty('operador_id')
    expect(body).not.toHaveProperty('hora_inicio')
    expect(body).not.toHaveProperty('hora_fin')
    expect(body).not.toHaveProperty('observaciones')
  })

  it('envía hora_inicio/hora_fin y operador_id cuando se llenan', async () => {
    const fetchMock = instalarFetch()
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '6' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Operador'), { target: { value: '7' } })
    fireEvent.change(screen.getByLabelText('Hora inicio'), { target: { value: '08:00' } })
    fireEvent.change(screen.getByLabelText('Hora fin'), { target: { value: '13:00' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))

    await waitFor(() => expect(payloadDe(fetchMock)).not.toBeNull())
    const body = payloadDe(fetchMock)
    expect(body).toMatchObject({ obra_id: 6, operador_id: 7, hora_inicio: '08:00', hora_fin: '13:00', horas_trabajadas: 5 })
  })

  it('sin horas no dispara el POST (validación cliente)', async () => {
    const fetchMock = instalarFetch()
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    expect(payloadDe(fetchMock)).toBeNull()
  })

  it('toast de éxito muestra lo registrado y el TOTAL DEL DÍA que devuelve el backend', async () => {
    instalarFetch() // respuesta con horas_trabajadas del día = 8
    const onExito = vi.fn()
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={onExito} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))

    await waitFor(() => expect(onExito).toHaveBeenCalled())
    expect(toast.success).toHaveBeenCalledWith('5 h registradas · total del día 8 h')
  })

  it('replay: true → aviso informativo, sin toast de éxito (idempotencia, no duplica)', async () => {
    instalarFetch({ postResp: jsonResp({ replay: true, turnos: [], horas_trabajadas: '8.0000' }, 200) })
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={vi.fn()} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))

    await waitFor(() => expect(toast.message).toHaveBeenCalledWith('Ya estaba registrado'))
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('muestra el detail del backend ante un 409 (sin asignación activa)', async () => {
    const detalle = 'La máquina no tiene una asignación activa que cubra la fecha'
    instalarFetch({ postResp: jsonResp({ detail: detalle }, 409) })
    const onExito = vi.fn()
    render(<FormRegistroHoras maquinaFija={MAQUINA} onExito={onExito} onCancelar={vi.fn()} />)

    fireEvent.change(await screen.findByLabelText('Obra'), { target: { value: '5' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: /Registrar horas/i }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detalle))
    expect(onExito).not.toHaveBeenCalled()
  })
})
