import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import TabRetenciones from './TabRetenciones.jsx'
import { USER_KEY } from '@/lib/api'
import { conQuery } from '@/test/query.jsx'

const REGLAS = [
  { id: 1, tipo: 'retefuente', concepto: 'Compras generales', base_minima_uvt: '27', tarifa: '2.5', activo: true, editable: true },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url)
    if (u.includes('/retenciones/config') && opts.method === 'PUT') {
      return Promise.resolve(jsonResp({ id: 2, tipo: 'ica', concepto: 'Servicios', base_minima_uvt: '0', tarifa: '0.7', activo: true, editable: true }))
    }
    if (u.includes('/retenciones/config')) return Promise.resolve(jsonResp(REGLAS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function comoAdmin() { localStorage.setItem(USER_KEY, JSON.stringify({ id: 1, rol: 'admin', tenant: 'pr' })) }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabRetenciones', () => {
  it('sin rol admin muestra el aviso', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabRetenciones /></MemoryRouter>))
    expect(screen.getByText(/solo para administradores/i)).toBeInTheDocument()
  })

  it('admin ve el catálogo y guarda una nueva regla (PUT)', async () => {
    comoAdmin()
    const fetchMock = instalarFetch()
    render(conQuery(<MemoryRouter><TabRetenciones /></MemoryRouter>))

    expect(await screen.findByText('Compras generales')).toBeInTheDocument()
    expect(screen.getByText('2.5%')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Concepto'), { target: { value: 'Servicios' } })
    fireEvent.change(screen.getByLabelText('Tarifa (%)'), { target: { value: '0.7' } })
    fireEvent.click(screen.getByRole('button', { name: /Guardar regla/ }))

    await screen.findByText('Compras generales')
    const llamada = fetchMock.mock.calls.find(c => String(c[0]).includes('/retenciones/config') && c[1]?.method === 'PUT')
    expect(llamada).toBeTruthy()
    expect(JSON.parse(llamada[1].body).concepto).toBe('Servicios')
  })
})
