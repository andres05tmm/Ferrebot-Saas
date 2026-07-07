import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import TabTrabajadores from './TabTrabajadores.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const DIRECTO = {
  id: 1, tipo_vinculacion: 'DIRECTO', tipo_documento: 'CC', documento: '1000123456',
  nombres: 'Juan Camilo', apellidos: 'Ríos Vélez', cargo: 'Operador vibrocompactador',
  telefono: null, email: null, activo: true, salario_base: '1750905.0000', tarifa_hora: null,
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function render_() { return render(<MemoryRouter><TabTrabajadores /></MemoryRouter>) }

describe('TabTrabajadores', () => {
  it('lista al personal con su tipo de vinculación como badge', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([DIRECTO]))))
    render_()
    expect(await screen.findByText('Juan Camilo Ríos Vélez')).toBeInTheDocument()
    expect(screen.getByText('Directo')).toBeInTheDocument()
    expect(screen.getByText('Operador vibrocompactador')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando no hay personal', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResp([]))))
    render_()
    expect(await screen.findByText('Aún no hay personal registrado')).toBeInTheDocument()
    expect(screen.getByText('Registrar el primer trabajador')).toBeInTheDocument()
  })

  it('crear trabajador DIRECTO postea el shape correcto (POST /trabajadores)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (String(url).includes('/trabajadores') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 2 }, 201))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nuevo trabajador'))
    fireEvent.change(await screen.findByLabelText('Nombres'), { target: { value: 'Ana' } })
    fireEvent.change(screen.getByLabelText('Apellidos'), { target: { value: 'Gómez' } })
    fireEvent.change(screen.getByLabelText('Cargo'), { target: { value: 'Ayudante' } })
    fireEvent.change(screen.getByLabelText('Documento'), { target: { value: '555' } })
    fireEvent.change(screen.getByLabelText('Salario base'), { target: { value: '1750905' } })
    fireEvent.click(screen.getByText('Crear trabajador'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/trabajadores') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        tipo_vinculacion: 'DIRECTO', tipo_documento: 'CC', documento: '555',
        nombres: 'Ana', apellidos: 'Gómez', cargo: 'Ayudante',
        salario_base: 1750905, tarifa_hora: null,
      })
    })
  })

  it('al cambiar a PATACALIENTE pide tarifa por hora y la postea (salario_base null)', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (String(url).includes('/trabajadores') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 3 }, 201))
      return Promise.resolve(jsonResp([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    render_()

    fireEvent.click(await screen.findByText('Nuevo trabajador'))
    fireEvent.click(screen.getByText('Patacaliente'))
    fireEvent.change(await screen.findByLabelText('Nombres'), { target: { value: 'Beto' } })
    fireEvent.change(screen.getByLabelText('Apellidos'), { target: { value: 'Pérez' } })
    fireEvent.change(screen.getByLabelText('Cargo'), { target: { value: 'Jornalero' } })
    fireEvent.change(screen.getByLabelText('Documento'), { target: { value: '777' } })
    fireEvent.change(screen.getByLabelText('Tarifa por hora'), { target: { value: '12000' } })
    fireEvent.click(screen.getByText('Crear trabajador'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/trabajadores') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({
        tipo_vinculacion: 'PATACALIENTE', documento: '777', tarifa_hora: 12000, salario_base: null,
      })
    })
  })

  it('la ruta /trabajadores se gatea por la feature `nomina` (y el meta-pack construccion)', () => {
    expect(isRouteEnabled('/trabajadores', [])).toBe(false)
    expect(isRouteEnabled('/trabajadores', ['nomina'])).toBe(true)
    expect(isRouteEnabled('/trabajadores', ['construccion'])).toBe(true)
  })
})
