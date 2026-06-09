import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import CrearTenantForm from './CrearTenantForm.jsx'

function jsonResp(data, status = 200) {
  return { ok: status < 400, status, json: async () => data }
}

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('CrearTenantForm (arma manifiesto + encola + polling)', () => {
  it('arma el manifiesto, postea a /admin/tenants y al hacer polling muestra ok + resumen', async () => {
    const fetchMock = vi.fn((url, opts = {}) => {
      const u = String(url)
      if (u.includes('/admin/tenants') && opts.method === 'POST') return Promise.resolve(jsonResp({ job_id: 'J1' }, 202))
      if (u.includes('/admin/jobs/J1')) {
        return Promise.resolve(jsonResp({ estado: 'ok', slug: 'clinica-x', resumen: 'provision_manifest: clinica-x OK -> ' }))
      }
      return Promise.resolve(jsonResp({}, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<CrearTenantForm intervaloMs={5} />)
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'clinica-x' } })
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Clínica X' } })
    fireEvent.change(screen.getByLabelText('NIT'), { target: { value: 'NIT-9' } })
    fireEvent.change(screen.getByLabelText('Email del admin'), { target: { value: 'due@x.co' } })
    fireEvent.click(screen.getByLabelText('POS (ventas/inventario/caja)'))
    fireEvent.click(screen.getByRole('button', { name: 'Crear empresa' }))

    // POST con el manifiesto armado.
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/admin/tenants') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toEqual({
        version: 1,
        identidad: { slug: 'clinica-x', nombre: 'Clínica X', nit: 'NIT-9' },
        admin: { email: 'due@x.co' },
        plan: { nombre: 'Custom', features: ['pos'] },
      })
    })

    // Polling refleja el estado terminal con el resumen.
    expect(await screen.findByText(/provision_manifest: clinica-x OK/)).toBeInTheDocument()
  })

  it('manifiesto rechazado por el backend (422) muestra el motivo y NO entra en polling', async () => {
    const fetchMock = vi.fn((url, opts = {}) => {
      if (String(url).includes('/admin/tenants') && opts.method === 'POST') {
        return Promise.resolve(jsonResp({ detail: 'slug inválido' }, 422))
      }
      return Promise.resolve(jsonResp({}, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<CrearTenantForm intervaloMs={5} />)
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'Mala' } })
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('NIT'), { target: { value: 'N' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear empresa' }))

    expect(await screen.findByText(/Manifiesto inválido: slug inválido/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/admin/jobs/'))).toBe(false)
  })

  it('un 422 de Pydantic (detail = array de {loc,msg}) se muestra legible, no [object Object]', async () => {
    const fetchMock = vi.fn((url, opts = {}) => {
      if (String(url).includes('/admin/tenants') && opts.method === 'POST') {
        return Promise.resolve(jsonResp({
          detail: [
            { loc: ['body', 'identidad', 'slug'], msg: 'String should match pattern', type: 'string_pattern_mismatch' },
            { loc: ['body', 'identidad', 'nit'], msg: 'Field required', type: 'missing' },
          ],
        }, 422))
      }
      return Promise.resolve(jsonResp({}, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<CrearTenantForm intervaloMs={5} />)
    fireEvent.change(screen.getByLabelText('Slug'), { target: { value: 'Mala' } })
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('NIT'), { target: { value: 'N' } })
    fireEvent.click(screen.getByRole('button', { name: 'Crear empresa' }))

    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent('String should match pattern; Field required')
    expect(alerta).not.toHaveTextContent('[object Object]')
  })
})
