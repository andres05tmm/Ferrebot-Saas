import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import TenantManage from './TenantManage.jsx'

function jsonResp(data, status = 200) {
  return { ok: status < 400, status, json: async () => data }
}

const TENANT = { slug: 'pr', nombre: 'Punto Rojo', estado: 'activa', features: ['pos'] }

beforeEach(() => localStorage.clear())
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TenantManage', () => {
  it('toggle de feature pega a PUT /admin/tenants/{slug}/features con {feature, habilitada}', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp({ slug: 'pr', features: ['pos', 'fiados'] })))
    vi.stubGlobal('fetch', fetchMock)
    const onCambio = vi.fn()

    render(<TenantManage tenant={TENANT} onCambio={onCambio} />)
    // 'fiados' no está activa → al hacer clic se ACTIVA (habilitada: true).
    fireEvent.click(screen.getByLabelText('toggle fiados'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/admin/tenants/pr/features'))
      expect(call).toBeTruthy()
      expect(call[1].method).toBe('PUT')
      expect(JSON.parse(call[1].body)).toEqual({ feature: 'fiados', habilitada: true })
    })
    await waitFor(() => expect(onCambio).toHaveBeenCalled())
  })

  it('genera el enlace de set-password (POST identidad-admin) y lo muestra para copiar', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResp({ identidad_id: 1, set_password_token: 'TOK123' })))
    vi.stubGlobal('fetch', fetchMock)

    render(<TenantManage tenant={TENANT} onCambio={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Email del admin'), { target: { value: 'due@pr.co' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generar enlace' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/admin/tenants/pr/identidad-admin'))
      expect(call).toBeTruthy()
      expect(call[1].method).toBe('POST')
      expect(JSON.parse(call[1].body)).toEqual({ email: 'due@pr.co' })
    })
    const enlace = await screen.findByLabelText('Enlace de set-password')
    expect(enlace.value).toContain('/set-password?token=TOK123')
  })
})
