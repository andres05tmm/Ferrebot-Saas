import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import { toast } from 'sonner'
import { FeaturesProvider } from '@/lib/features.jsx'
import TabClientes from './TabClientes.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ postStatus = 201 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    if (String(url).includes('/clientes/ciudades')) {
      return Promise.resolve(jsonResp([{ matias_id: '149', dane_code: 5001, nombre: 'Medellín', departamento: 'Antioquia' }]))
    }
    if (String(url).includes('/clientes/paises')) {
      return Promise.resolve(jsonResp([{ matias_id: 45, nombre: 'Colombia', codigo_a2: 'CO' }]))
    }
    if (String(url).includes('/clientes') && opts?.method === 'POST') {
      return Promise.resolve(jsonResp({ id: 9 }, postStatus))
    }
    if (String(url).includes('/clientes')) {
      return Promise.resolve(jsonResp([{ id: 1, nombre: 'Ana', documento: '111', telefono: null }]))
    }
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderTab(features = []) {
  return render(
    <MemoryRouter>
      <FeaturesProvider features={features}>
        <TabClientes />
      </FeaturesProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabClientes', () => {
  it('lista y filtra con ?q; sin la feature no muestra selectores fiscales', async () => {
    const fetchMock = instalarFetch()
    renderTab([])
    expect(await screen.findByText('Ana')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Buscar cliente'), { target: { value: 'an' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes?q=an'))).toBe(true)
    })
    expect(screen.queryByLabelText('Buscar ciudad')).toBeNull()  // feature off → sin fiscal
  })

  it('alta crea (POST /clientes) y avisa dedup cuando ya existe (200)', async () => {
    const fetchMock = instalarFetch({ postStatus: 200 })
    renderTab([])
    await screen.findByText('Ana')

    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Beto' } })
    fireEvent.change(screen.getByLabelText('Documento'), { target: { value: '222' } })
    fireEvent.click(screen.getByText('Crear cliente'))

    await waitFor(() => expect(toast.message).toHaveBeenCalled())  // 200 → "ya existe"
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/clientes') && c[1]?.method === 'POST')
    expect(JSON.parse(call[1].body)).toMatchObject({ nombre: 'Beto', documento: '222', tipo_documento: 'CC' })
  })

  it('con la feature on muestra el selector de ciudad y llama /clientes/ciudades?q', async () => {
    const fetchMock = instalarFetch()
    renderTab(['facturacion_electronica'])
    await screen.findByText('Ana')

    const ciudad = screen.getByLabelText('Buscar ciudad')   // visible solo con la feature
    fireEvent.change(ciudad, { target: { value: 'mede' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes/ciudades') && String(c[0]).includes('q=mede'))).toBe(true)
    })
  })
})
