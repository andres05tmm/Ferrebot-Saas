import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { isRouteEnabled } from '@/lib/features.jsx'
import { USER_KEY } from '@/lib/api'
import TabConocimiento from './TabConocimiento.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

const ENTRADA = {
  id: 1, titulo: 'Horarios', contenido: 'Lunes a viernes de 8am a 6pm', activo: true, orden: 0,
  creado_en: '2026-06-10T10:00:00-05:00', actualizado_en: null,
}

function instalarFetch({ entradas } = {}) {
  const calls = []
  const fetchMock = vi.fn((url, opts = {}) => {
    const u = String(url); const m = opts.method || 'GET'
    calls.push([u, m, opts.body])
    if (u.includes('/faq/conocimiento') && m === 'POST') return Promise.resolve(jsonResp({ id: 9 }, 201))
    if (/\/faq\/conocimiento\/\d+/.test(u) && m === 'PUT') return Promise.resolve(jsonResp({ id: 1 }, 200))
    if (u.includes('/faq/conocimiento')) return Promise.resolve(jsonResp(entradas ?? [ENTRADA]))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls }
}

function renderTab({ admin = true } = {}) {
  if (admin) localStorage.setItem(USER_KEY, JSON.stringify({ rol: 'admin' }))
  return render(<MemoryRouter><TabConocimiento /></MemoryRouter>)
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabConocimiento', () => {
  it('gating de ruta: oculto sin pack_faq, visible con la feature', () => {
    expect(isRouteEnabled('/conocimiento', [])).toBe(false)
    expect(isRouteEnabled('/conocimiento', ['pack_faq'])).toBe(true)
  })

  it('renderiza la lista de entradas', async () => {
    instalarFetch()
    renderTab()
    expect(await screen.findByText('Horarios')).toBeInTheDocument()
  })

  it('estado vacío cuando no hay entradas', async () => {
    instalarFetch({ entradas: [] })
    renderTab()
    expect(await screen.findByText(/Aún no hay información cargada/i)).toBeInTheDocument()
  })

  it('crear llama POST /faq/conocimiento', async () => {
    const { calls } = instalarFetch({ entradas: [] })
    renderTab()
    fireEvent.change(await screen.findByLabelText('Título'), { target: { value: 'Ubicación' } })
    fireEvent.change(screen.getByLabelText('Contenido'), { target: { value: 'Cra 1 # 2-3' } })
    fireEvent.click(screen.getByText('Crear entrada'))
    await waitFor(() => {
      const call = calls.find(([u, m]) => u.includes('/faq/conocimiento') && m === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[2])).toMatchObject({ titulo: 'Ubicación', contenido: 'Cra 1 # 2-3' })
    })
  })

  it('editar llama PUT /faq/conocimiento/{id}', async () => {
    const { calls } = instalarFetch()
    renderTab()
    fireEvent.click(await screen.findByText('Editar'))
    fireEvent.change(screen.getByLabelText('Contenido'), { target: { value: 'Lunes a sábado' } })
    fireEvent.click(screen.getByText('Guardar'))
    await waitFor(() => {
      const call = calls.find(([u, m]) => /\/faq\/conocimiento\/1$/.test(u) && m === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[2]).contenido).toBe('Lunes a sábado')
    })
  })

  it('staff (no admin) ve la lista pero no el formulario de edición', async () => {
    instalarFetch()
    renderTab({ admin: false })  // sin USER_KEY admin
    expect(await screen.findByText('Horarios')).toBeInTheDocument()
    expect(screen.queryByText('Nueva entrada')).toBeNull()
    expect(screen.getByText(/Solo un administrador/i)).toBeInTheDocument()
  })
})
