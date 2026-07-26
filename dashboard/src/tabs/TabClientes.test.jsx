import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

// Rol controlable: solo el admin ve el botón de eliminar.
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import { toast } from 'sonner'
import { FeaturesProvider } from '@/lib/features.jsx'
import TabClientes from './TabClientes.jsx'

const CLIENTES = [
  { id: 1, nombre: 'Ana Gómez', tipo_documento: 'CC', documento: '111', telefono: '300', correo: null, direccion: 'Calle 1', ciudad_dane: null, regimen: null },
  { id: 2, nombre: 'Ferre La 80 SAS', tipo_documento: 'NIT', documento: '900123', telefono: null, correo: 'a@b.co', direccion: null, ciudad_dane: '5001', regimen: '1' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch({ postStatus = 201, deleteStatus = 204 } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/clientes/ciudades')) {
      return Promise.resolve(jsonResp([{ matias_id: '149', dane_code: 5001, nombre: 'Medellín', departamento: 'Antioquia' }]))
    }
    if (u.includes('/clientes/paises')) return Promise.resolve(jsonResp([{ matias_id: 45, nombre: 'Colombia', codigo_a2: 'CO' }]))
    if (u.includes('/clientes') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }, postStatus))
    if (u.includes('/clientes') && opts?.method === 'PUT') return Promise.resolve(jsonResp({ id: 1 }, 200))
    if (u.includes('/clientes') && opts?.method === 'DELETE') return Promise.resolve(jsonResp(null, deleteStatus))
    if (u.includes('/clientes')) return Promise.resolve(jsonResp(CLIENTES))
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

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabClientes — listado', () => {
  it('pinta la tabla con tipo de persona derivado del documento y busca con ?q (debounce)', async () => {
    const fetchMock = instalarFetch()
    renderTab([])
    expect(await screen.findByText('Ana Gómez')).toBeInTheDocument()
    // NIT → Jurídica; el resto → Natural (mismo criterio que la facturación).
    expect(screen.getByText('Jurídica')).toBeInTheDocument()
    expect(screen.getByText('Natural')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Buscar cliente'), { target: { value: 'an' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes?q=an'))).toBe(true)
    }, { timeout: 2000 })
  })

  it('un vendedor no ve el botón de eliminar', async () => {
    authState.admin = false
    instalarFetch()
    renderTab([])
    await screen.findByText('Ana Gómez')
    expect(screen.queryByTitle('Eliminar cliente')).toBeNull()
    expect(screen.getAllByTitle('Editar cliente').length).toBeGreaterThan(0)
  })
})

describe('TabClientes — modal de alta/edición', () => {
  it('crea con POST y avisa la dedup cuando el documento ya existía (200)', async () => {
    const fetchMock = instalarFetch({ postStatus: 200 })
    renderTab([])
    await screen.findByText('Ana Gómez')

    fireEvent.click(screen.getByText('Nuevo cliente'))
    fireEvent.change(await screen.findByLabelText('Nombre'), { target: { value: 'Beto' } })
    fireEvent.change(screen.getByLabelText('Documento'), { target: { value: '222' } })
    fireEvent.click(screen.getByText('Crear cliente'))

    await waitFor(() => expect(toast.message).toHaveBeenCalled())
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/clientes') && c[1]?.method === 'POST')
    expect(JSON.parse(call[1].body)).toMatchObject({ nombre: 'Beto', documento: '222', tipo_documento: 'CC' })
  })

  it('editar precarga el cliente y guarda con PUT /clientes/{id}', async () => {
    const fetchMock = instalarFetch()
    renderTab([])
    await screen.findByText('Ana Gómez')

    fireEvent.click(screen.getAllByTitle('Editar cliente')[0])
    const nombre = await screen.findByLabelText('Nombre')
    expect(nombre.value).toBe('Ana Gómez')
    fireEvent.change(nombre, { target: { value: 'Ana G.' } })
    fireEvent.click(screen.getByText('Guardar cambios'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/clientes/1') && c[1]?.method === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({ nombre: 'Ana G.' })
    })
  })

  it('sin la feature fiscal no hay ciudad ni régimen', async () => {
    instalarFetch()
    renderTab([])
    await screen.findByText('Ana Gómez')
    fireEvent.click(screen.getByText('Nuevo cliente'))
    await screen.findByLabelText('Nombre')

    expect(screen.queryByLabelText('Buscar ciudad')).toBeNull()
    expect(screen.queryByText('Régimen fiscal')).toBeNull()
  })

  it('con la feature fiscal el buscador de ciudad trae opciones y al elegir guarda el DANE', async () => {
    const fetchMock = instalarFetch()
    renderTab(['facturacion_electronica'])
    await screen.findByText('Ana Gómez')

    fireEvent.click(screen.getByText('Nuevo cliente'))
    fireEvent.change(await screen.findByLabelText('Nombre'), { target: { value: 'Beto' } })
    fireEvent.change(screen.getByLabelText('Buscar ciudad'), { target: { value: 'mede' } })

    // Debounce de 300 ms y mínimo 2 letras: la opción aparece en la lista desplegable.
    const opcion = await screen.findByText('Medellín', {}, { timeout: 2000 })
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes/ciudades') && String(c[0]).includes('q=mede'))).toBe(true)
    fireEvent.mouseDown(opcion)

    // Régimen como selector de dos opciones (no texto libre) con los literales que lee la facturación.
    fireEvent.click(screen.getByText('Responsable de IVA'))
    fireEvent.click(screen.getByText('Crear cliente'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/clientes') && c[1]?.method === 'POST')
      expect(JSON.parse(call[1].body)).toMatchObject({ ciudad_dane: '5001', regimen: 'responsable_iva' })
    })
  })

  it('una letra NO dispara la búsqueda de ciudades (mínimo 2)', async () => {
    const fetchMock = instalarFetch()
    renderTab(['facturacion_electronica'])
    await screen.findByText('Ana Gómez')
    fireEvent.click(screen.getByText('Nuevo cliente'))
    fireEvent.change(await screen.findByLabelText('Buscar ciudad'), { target: { value: 'm' } })

    await new Promise(r => setTimeout(r, 400))
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes/ciudades'))).toBe(false)
  })
})

describe('TabClientes — eliminar', () => {
  it('confirma y hace DELETE; con 409 avisa que tiene ventas', async () => {
    const fetchMock = instalarFetch({ deleteStatus: 409 })
    renderTab([])
    await screen.findByText('Ana Gómez')

    fireEvent.click(screen.getAllByTitle('Eliminar cliente')[0])
    fireEvent.click(await screen.findByText('Sí, eliminar'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/clientes/1') && c[1]?.method === 'DELETE')).toBe(true)
    })
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(expect.stringMatching(/ventas o fiados/i)))
  })
})
